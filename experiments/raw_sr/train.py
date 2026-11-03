import argparse
from pathlib import Path
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from tqdm import tqdm
import math
import sys
sys.path.insert(0, str(Path(__file__).parent))

from dataset import MONO_MIX, BayerPatchDataset
from model import BayerSwin2SR
from model_mono import BayerSwin2SRMono

class EdgeLoss(nn.Module):
    def __init__(self, channels=3):
        super().__init__()
        self.channels = channels
        k = torch.tensor([[-1., -2., -1.], [0., 0., 0.], [1., 2., 1.]])
        self.register_buffer('weight_x', k.view(1, 1, 3, 3).repeat(channels, 1, 1, 1) / 4.)
        self.register_buffer('weight_y', k.t().view(1, 1, 3, 3).repeat(channels, 1, 1, 1) / 4.)

    def forward(self, pred, target):
        C = pred.shape[1]
        if C != self.channels:
            k = torch.tensor([[-1., -2., -1.], [0., 0., 0.], [1., 2., 1.]], device=pred.device)
            wx = k.view(1, 1, 3, 3).repeat(C, 1, 1, 1) / 4.
            wy = k.t().view(1, 1, 3, 3).repeat(C, 1, 1, 1) / 4.
        else:
            wx = self.weight_x
            wy = self.weight_y
        
        pred_pad = F.pad(pred, (1, 1, 1, 1), mode='replicate')
        target_pad = F.pad(target, (1, 1, 1, 1), mode='replicate')
        
        pred_x = F.conv2d(pred_pad, wx, groups=C)
        pred_y = F.conv2d(pred_pad, wy, groups=C)
        target_x = F.conv2d(target_pad, wx, groups=C)
        target_y = F.conv2d(target_pad, wy, groups=C)
        
        return F.l1_loss(pred_x, target_x) + F.l1_loss(pred_y, target_y)

def gaussian_blur(x, sigma):
    """Separable Gaussian blur per channel, replicate-padded."""
    r = max(1, int(math.ceil(3 * sigma)))
    t = torch.arange(-r, r + 1, device=x.device, dtype=x.dtype)
    k = torch.exp(-(t ** 2) / (2 * sigma ** 2))
    k = k / k.sum()
    C = x.shape[1]
    x = F.conv2d(F.pad(x, (r, r, 0, 0), mode="replicate"), k.view(1, 1, 1, -1).repeat(C, 1, 1, 1), groups=C)
    return F.conv2d(F.pad(x, (0, 0, r, r), mode="replicate"), k.view(1, 1, -1, 1).repeat(C, 1, 1, 1), groups=C)


class TrainLoss(nn.Module):
    """L1 on brightness and two colour differences, plus L1 on Sobel gradients (as EdgeLoss), so
    noise the model cannot avoid lands in brightness (grain) rather than colour (digital speckle):

    - brightness: MONO_MIX of RGB; colour: R − G and B − G, weighted chroma_weight / 2 each and
      compared against the target blurred by chroma_blur (colour detail matters less, and fine
      structure in colour is then an error, so the output's colour stays smooth);
    - shadow_weight: errors (pixel and edge) cost up to 1 + shadow_weight times as much below
      mid-grey, where noise looks muddiest.
    With chroma_weight 1, chroma_blur 0 and shadow_weight 0 it is plain L1 + edge_weight × EdgeLoss.
    Measured on held-out patches (compare_models.py): at denoise 0.1, colour noise 38 % lower with
    3 / 1 / 1; brightness grain, colour and PSNR unchanged. Returns (loss, {pixel, edge})."""

    def __init__(self, channels, edge_weight=1.0, chroma_weight=3.0, chroma_blur=1.0, shadow_weight=1.0):
        super().__init__()
        self.channels, self.ew, self.cw, self.cb, self.sw = channels, edge_weight, chroma_weight, chroma_blur, shadow_weight
        self.register_buffer("mix", torch.tensor(MONO_MIX, dtype=torch.float32).view(1, 3, 1, 1))
        k = torch.tensor([[-1., -2., -1.], [0., 0., 0.], [1., 2., 1.]]) / 4
        self.register_buffer("kx", k.view(1, 1, 3, 3).repeat(channels, 1, 1, 1))
        self.register_buffer("ky", k.t().contiguous().view(1, 1, 3, 3).repeat(channels, 1, 1, 1))

    def brightness(self, img):
        return img if self.channels == 1 else (img * self.mix).sum(dim=1, keepdim=True)

    def pixel(self, out, tgt):
        if self.channels == 1 or (self.cw == 1 and self.cb == 0):
            return (out - tgt).abs().mean(dim=1, keepdim=True)
        co = torch.cat([out[:, :1] - out[:, 1:2], out[:, 2:3] - out[:, 1:2]], dim=1)
        ct = torch.cat([tgt[:, :1] - tgt[:, 1:2], tgt[:, 2:3] - tgt[:, 1:2]], dim=1)
        if self.cb > 0:
            ct = gaussian_blur(ct, self.cb)
        return (self.brightness(out) - self.brightness(tgt)).abs() + self.cw * 0.5 * (co - ct).abs().mean(dim=1, keepdim=True)

    def edge(self, out, tgt):
        po, pt = F.pad(out, (1, 1, 1, 1), mode="replicate"), F.pad(tgt, (1, 1, 1, 1), mode="replicate")
        C = self.channels
        dx = F.conv2d(po, self.kx, groups=C) - F.conv2d(pt, self.kx, groups=C)
        dy = F.conv2d(po, self.ky, groups=C) - F.conv2d(pt, self.ky, groups=C)
        return (dx.abs() + dy.abs()).mean(dim=1, keepdim=True)

    def forward(self, out, tgt):
        out, tgt = out.float(), tgt.float()
        pix, edge = self.pixel(out, tgt), self.edge(out, tgt)
        err = pix + self.ew * edge
        if self.sw:
            err = err * (1 + self.sw * (1 - self.brightness(tgt) / 0.5).clamp(0, 1))
        return err.mean(), {"pixel": pix.mean().detach(), "edge": edge.mean().detach()}


def compute_sharpness_metrics(pred, target):
    """
    Computes:
      1. psnr: standard peak signal to noise ratio (dB)
      2. edge_psnr: PSNR on Sobel gradient magnitudes (dB)
      3. sharpness_ratio: Var(Laplacian(pred)) / Var(Laplacian(target)) (target is 1.0)
    """
    if pred.shape[1] == 3:
        weights = torch.tensor([0.2989, 0.5870, 0.1140], device=pred.device).view(1, 3, 1, 1)
        pred_g = (pred * weights).sum(dim=1, keepdim=True)
        target_g = (target * weights).sum(dim=1, keepdim=True)
    else:
        pred_g = pred
        target_g = target

    # 1. Standard PSNR
    mse = F.mse_loss(pred, target).item()
    psnr = -10 * math.log10(mse + 1e-8) if mse > 0 else 100.0

    # 2. Edge PSNR via Sobel
    kx = torch.tensor([[-1., 0., 1.], [-2., 0., 2.], [-1., 0., 1.]], device=pred.device).view(1, 1, 3, 3) / 4.0
    ky = torch.tensor([[-1., -2., -1.], [0., 0., 0.], [1., 2., 1.]], device=pred.device).view(1, 1, 3, 3) / 4.0
    
    gx_p = F.conv2d(pred_g, kx, padding=1)
    gy_p = F.conv2d(pred_g, ky, padding=1)
    mag_p = torch.sqrt(gx_p**2 + gy_p**2 + 1e-8)

    gx_t = F.conv2d(target_g, kx, padding=1)
    gy_t = F.conv2d(target_g, ky, padding=1)
    mag_t = torch.sqrt(gx_t**2 + gy_t**2 + 1e-8)

    edge_mse = F.mse_loss(mag_p, mag_t).item()
    data_range = max(mag_t.max().item(), 1.0)
    edge_psnr = 20 * math.log10(data_range) - 10 * math.log10(edge_mse + 1e-8)

    # 3. Laplacian Sharpness Ratio
    k_lap = torch.tensor([[0., 1., 0.], [1., -4., 1.], [0., 1., 0.]], device=pred.device).view(1, 1, 3, 3)
    lap_p = F.conv2d(pred_g, k_lap, padding=1)
    lap_t = F.conv2d(target_g, k_lap, padding=1)
    
    var_p = torch.var(lap_p, dim=(-2, -1))
    var_t = torch.var(lap_t, dim=(-2, -1))
    sharpness_ratio = (var_p / (var_t + 1e-7)).mean().item()

    return psnr, edge_psnr, sharpness_ratio

def save_val_preview(val_x, val_y, val_pred, save_path, epoch, is_mono=False):
    """
    Saves a side-by-side validation strip:
    [Bayer Input (4x) | Model Prediction | Ground Truth Target]
    """
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    # Take first sample in batch
    vx = val_x[0].detach().cpu().float().numpy()  # (4, H_in, W_in)
    vy = val_y[0].detach().cpu().float().numpy()  # (1, H, W) or (3, H, W)
    vp = val_pred[0].detach().cpu().float().numpy()  # (1, H, W) or (3, H, W)

    H, W = vy.shape[1:]

    if is_mono:
        pred_arr = (np.clip(vp[0], 0, 1) * 255).astype(np.uint8)
        tgt_arr = (np.clip(vy[0], 0, 1) * 255).astype(np.uint8)
        in_g = cv2.resize(vx[1], (W, H), interpolation=cv2.INTER_NEAREST)
        in_arr = (np.clip(in_g, 0, 1) * 255).astype(np.uint8)
        
        panels = [
            ("Input Quad (4x)", in_arr),
            (f"Model Pred (Ep {epoch})", pred_arr),
            ("Ground Truth", tgt_arr)
        ]
    else:
        pred_arr = (np.clip(np.moveaxis(vp, 0, -1), 0, 1) * 255).astype(np.uint8)
        tgt_arr = (np.clip(np.moveaxis(vy, 0, -1), 0, 1) * 255).astype(np.uint8)
        
        in_rgb = np.stack([vx[0], 0.5 * (vx[1] + vx[2]), vx[3]], axis=-1)
        in_rgb_up = cv2.resize(in_rgb, (W, H), interpolation=cv2.INTER_NEAREST)
        in_arr = (np.clip(in_rgb_up, 0, 1) * 255).astype(np.uint8)

        panels = [
            ("Input Quad (4x)", in_arr),
            (f"Model Pred (Ep {epoch})", pred_arr),
            ("Ground Truth", tgt_arr)
        ]

    header = 26
    strip = Image.new("RGB", (W * len(panels) + (len(panels) - 1) * 4, H + header), (30, 30, 30))
    draw = ImageDraw.Draw(strip)

    for i, (label, arr) in enumerate(panels):
        im = Image.fromarray(arr).convert("RGB")
        x_off = i * (W + 4)
        strip.paste(im, (x_off, header))
        draw.text((x_off + 6, 6), label, fill=(240, 240, 240))

    save_path.parent.mkdir(parents=True, exist_ok=True)
    strip.save(save_path)
    latest_path = save_path.parent / "val_latest.png"
    strip.save(latest_path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--patches-dir", type=Path, default=Path("experiments/raw_sr/data_binned"), help="train/ and val/ patches from prepare_dataset.py")
    parser.add_argument("--monochrome", action="store_true", help="Train the monochrome model (BayerSwin2SRMono) with 1-channel target")
    parser.add_argument("--save-val-image", action="store_true", help="Save a side-by-side validation preview (Input / Model Pred / Target) every epoch")
    parser.add_argument("--val-preview-dir", type=Path, default=None, help="Directory to save validation preview images (default: <save-dir>/val_previews)")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--first-conv-lr-mult", type=float, default=1.0, help="Learning rate multiplier for the 4-channel first_conv layer (default 1.0)")
    parser.add_argument("--random-first-conv", action="store_true", help="Initialize first_conv randomly with Kaiming Normal (no RGB bias)")
    parser.add_argument("--edge-weight", type=float, default=1.0, help="Weight of EdgeLoss relative to L1")
    parser.add_argument("--keep-noise-luma", action=argparse.BooleanOptionalAction, default=True, help="Noise kept by partial denoising is brightness only: less denoising keeps grain, not coloured speckle")
    parser.add_argument("--chroma-weight", type=float, default=3.0, help="Weight of colour-difference errors relative to brightness (1: like plain L1)")
    parser.add_argument("--chroma-blur", type=float, default=1.0, help="Compare colour against the target blurred by this sigma (output px; 0: off)")
    parser.add_argument("--shadow-weight", type=float, default=1.0, help="Extra weight for errors in dark areas (0: off)")
    parser.add_argument("--noise-gain", type=float, nargs=2, default=[0.7, 3.0], metavar=("MIN", "MAX"), help="Input noise range, relative to one pixel of the photo (log-uniform)")
    parser.add_argument("--blur", type=float, nargs=2, default=[0.5, 3.5], metavar=("MIN", "MAX"), help="Input blur sigma range (target px), for the lens blur a native pixel has")
    parser.add_argument("--partial-denoise", type=float, default=0.5, help="Share of samples where only part of the noise is to be removed (the rest stays in the target), for upscale_raw.py --denoise")
    parser.add_argument("--mix-prob", type=float, default=0.7, help="Monochrome: share of samples with random channel gains (for other black-and-white mixes at inference)")
    parser.add_argument("--target-blur", type=float, default=0.5, help="Target blur as a fraction of the input's: 0 = learn full sharpening, 1 = none")
    parser.add_argument("--patch-size", type=int, default=256)
    parser.add_argument("--save-dir", type=Path, default=None)
    parser.add_argument("--init", type=Path, default=None, help="Start from this checkpoint (fine-tuning; older ones without the noise input load too)")
    parser.add_argument("--scheduler", type=str, default="cosine", choices=["cosine", "onecycle"], help="Learning rate schedule")
    parser.add_argument("--warmup-pct", type=float, default=0.08, help="Fraction of total steps dedicated to linear warmup")
    parser.add_argument("--min-lr", type=float, default=1e-6, help="Minimum learning rate at the end of cosine annealing")
    args = parser.parse_args()

    if args.save_dir is None:
        args.save_dir = Path("experiments/raw_sr/checkpoints_mono" if args.monochrome else "experiments/raw_sr/checkpoints")

    if args.val_preview_dir is not None:
        args.save_val_image = True
        val_preview_dir = args.val_preview_dir
    else:
        val_preview_dir = args.save_dir / "val_previews"

    args.save_dir.mkdir(parents=True, exist_ok=True)
    mode_str = "MONOCHROME (1-channel luminance target)" if args.monochrome else "RGB (3-channel target)"
    print(f"Mode: {mode_str} | Save Dir: {args.save_dir}")
    print(f"Loss: pixel (chroma ×{args.chroma_weight}, blur {args.chroma_blur}, shadows +{args.shadow_weight}) + {args.edge_weight}*EdgeLoss | "
          f"kept noise {'brightness only' if args.keep_noise_luma else 'per colour'} | Noise gain: {args.noise_gain} | Blur: {args.blur}, target ×{args.target_blur}")
    if args.save_val_image:
        val_preview_dir.mkdir(parents=True, exist_ok=True)
        print(f"Validation previews enabled: will save to {val_preview_dir}")

    ds_args = dict(patch_size=args.patch_size, monochrome=args.monochrome,
                   noise_gain=tuple(args.noise_gain), blur=tuple(args.blur), target_blur=args.target_blur,
                   mix_prob=args.mix_prob, partial_denoise=args.partial_denoise, keep_noise_luma=args.keep_noise_luma)
    train_ds = BayerPatchDataset(args.patches_dir / "train", is_train=True, **ds_args)
    val_ds = BayerPatchDataset(args.patches_dir / "val", is_train=False, **ds_args)
    print(f"Loaded {len(train_ds)} train patches, {len(val_ds)} val patches from {args.patches_dir}")
    num_workers = 6

    train_dl = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=num_workers, pin_memory=True, drop_last=True)
    val_dl = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=0)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if args.monochrome:
        model = BayerSwin2SRMono(random_first_conv=args.random_first_conv).to(device)
    else:
        model = BayerSwin2SR(random_first_conv=args.random_first_conv).to(device)
    if args.init:
        model.load_checkpoint(args.init)
        print(f"Initialised from {args.init}" + ("" if model.noise_conditioned else " (no noise input yet: it starts at zero)"))
    
    # Differential learning rate: first_conv adapts faster to Bayer phase offsets
    first_conv_params = list(model.swin2sr.first_convolution.parameters())
    other_params = [p for n, p in model.named_parameters() if 'first_convolution' not in n]

    optimizer = torch.optim.AdamW([
        {'params': other_params, 'lr': args.lr},
        {'params': first_conv_params, 'lr': args.lr * args.first_conv_lr_mult}
    ], weight_decay=1e-4)

    total_steps = args.epochs * len(train_dl)
    warmup_steps = max(1, int(total_steps * args.warmup_pct))

    if args.scheduler == "cosine":
        warmup_sched = torch.optim.lr_scheduler.LinearLR(
            optimizer, start_factor=0.01, end_factor=1.0, total_iters=warmup_steps
        )
        cosine_sched = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=max(1, total_steps - warmup_steps), eta_min=args.min_lr
        )
        scheduler = torch.optim.lr_scheduler.SequentialLR(
            optimizer, schedulers=[warmup_sched, cosine_sched], milestones=[warmup_steps]
        )
    else:
        scheduler = torch.optim.lr_scheduler.OneCycleLR(
            optimizer, max_lr=[args.lr, args.lr * args.first_conv_lr_mult],
            epochs=args.epochs, steps_per_epoch=len(train_dl),
            pct_start=args.warmup_pct, anneal_strategy='cos'
        )

    scaler = torch.amp.GradScaler('cuda' if torch.cuda.is_available() else 'cpu')
    channels = 1 if args.monochrome else 3
    loss_fn = TrainLoss(channels, args.edge_weight, args.chroma_weight, args.chroma_blur, args.shadow_weight).to(device)

    def compute_loss(out, y):
        return loss_fn(out, y)

    best_val_loss = float('inf')
    best_psnr = 0.0
    best_sharpness = 0.0

    print("=" * 95)
    print(f"{'Epoch':<8} | {'Train Loss':<12} | {'Val PSNR':<12} | {'Edge PSNR':<12} | {'Sharpness Ratio':<16} | {'Status'}")
    print("=" * 95)

    for epoch in range(args.epochs):
        model.train()
        train_loss = 0.0
        pbar = tqdm(train_dl, desc=f"Epoch {epoch+1}/{args.epochs}", leave=False)
        for x, y, n, _ in pbar:
            x, y, n = x.to(device), y.to(device), n.to(device)
            optimizer.zero_grad()

            with torch.amp.autocast('cuda' if torch.cuda.is_available() else 'cpu'):
                out = model(x, n)
                loss, parts = compute_loss(out, y)

            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()
            
            train_loss += loss.item()
            curr_lr = scheduler.get_last_lr()[0]
            pbar.set_postfix({**{k: f"{v.item():.4f}" for k, v in parts.items()}, "lr": f"{curr_lr:.2e}"})
            
        train_loss /= len(train_dl)
        
        # Validation
        model.eval()
        val_loss = 0.0
        psnr_list, edge_psnr_list, s_ratio_list = [], [], []

        with torch.no_grad():
            for i_val, (x, y, n, _) in enumerate(val_dl):
                x, y, n = x.to(device), y.to(device), n.to(device)
                with torch.amp.autocast('cuda' if torch.cuda.is_available() else 'cpu'):
                    out = model(x, n)
                    loss, _ = compute_loss(out, y)

                val_loss += loss.item()
                p, ep, sr = compute_sharpness_metrics(out, y)
                psnr_list.append(p)
                edge_psnr_list.append(ep)
                s_ratio_list.append(sr)

                if i_val == 0 and args.save_val_image:
                    preview_path = val_preview_dir / f"val_epoch_{epoch+1:03d}.png"
                    save_val_preview(x, y, out, preview_path, epoch+1, is_mono=args.monochrome)
                
        val_loss /= len(val_dl)
        mean_psnr = sum(psnr_list) / len(psnr_list)
        mean_edge_psnr = sum(edge_psnr_list) / len(edge_psnr_list)
        mean_sharpness = sum(s_ratio_list) / len(s_ratio_list)

        status_notes = []
        model.save_checkpoint(args.save_dir / "latest.pt")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            model.save_checkpoint(args.save_dir / "best_model.pt")
            status_notes.append("best_loss")

        if mean_psnr > best_psnr:
            best_psnr = mean_psnr
            model.save_checkpoint(args.save_dir / "best_psnr.pt")
            status_notes.append("best_psnr")

        # Track sharpness (highest sharpness ratio while avoiding noisy artifacts > 1.2)
        if mean_sharpness > best_sharpness and mean_sharpness <= 1.2:
            best_sharpness = mean_sharpness
            model.save_checkpoint(args.save_dir / "best_sharpness.pt")
            status_notes.append("best_sharp")

        status_str = ", ".join(status_notes) if status_notes else "-"
        print(f"Ep {epoch+1:02d}/{args.epochs:02d} | {train_loss:>10.4f}  | {mean_psnr:>8.2f} dB   | {mean_edge_psnr:>8.2f} dB   | {mean_sharpness:>9.3f} (1.000) | {status_str}")

if __name__ == "__main__":
    main()
