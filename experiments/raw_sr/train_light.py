"""Trains the lightweight Bayer model (model_light.py), separately from train.py.

    venv/bin/python experiments/raw_sr/train_light.py [--monochrome] [--teacher PT] ...

The same data (dataset.BayerPatchDataset on data_binned/) and loss (L1 and L1 on Sobel
gradients) as the Swin2SR model, so the two compare directly; trained from scratch. With
--teacher (a Swin2SR checkpoint of the same kind, .pt or .safetensors), the teacher's output
on the same input is a second target (--distill-weight): a small network learns faster from
a model's prediction than from noisy ground truth alone.
Checkpoints go to checkpoints_light/ (checkpoints_light_mono/): latest.pt and best_psnr.pt;
with --save-val-image, a preview per epoch (input, prediction, target), as train.py makes.
"""

import argparse
import math
import sys
from pathlib import Path

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
from tqdm import tqdm

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "src"))

from dataset import BayerPatchDataset  # noqa: E402
from model_light import ARCHS, LightBayerSR, seconds_per_frame  # noqa: E402
from train import EdgeLoss, save_val_preview  # noqa: E402


def psnr(out, y):
    mse = F.mse_loss(out.float().clamp(0, 1), y.float()).item()
    return -10 * math.log10(max(mse, 1e-12))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--patches-dir", type=Path, default=HERE / "data_binned")
    ap.add_argument("--monochrome", action="store_true")
    ap.add_argument("--arch", choices=ARCHS, default="span")
    ap.add_argument("--channels", type=int, default=48, help="feature channels (SPAN: 48 is the small size, 64 larger)")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--min-lr", type=float, default=1e-6)
    ap.add_argument("--warmup-pct", type=float, default=0.03)
    ap.add_argument("--patch-size", type=int, default=256)
    ap.add_argument("--edge-weight", type=float, default=1.0)
    ap.add_argument("--teacher", type=str, default=None, help="Swin2SR checkpoint of the same kind (distillation)")
    ap.add_argument("--distill-weight", type=float, default=0.5)
    # The data, as train.py
    ap.add_argument("--noise-gain", type=float, nargs=2, default=[0.7, 3.0])
    ap.add_argument("--blur", type=float, nargs=2, default=[0.5, 3.5])
    ap.add_argument("--target-blur", type=float, default=0.5)
    ap.add_argument("--partial-denoise", type=float, default=0.6)
    ap.add_argument("--mix-prob", type=float, default=0.7)
    ap.add_argument("--save-dir", type=Path, default=None)
    ap.add_argument("--init", type=Path, default=None, help="continue from a light checkpoint")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--save-val-image", action="store_true", help="save a validation preview (input / prediction / target) every epoch")
    ap.add_argument("--val-preview-dir", type=Path, default=None, help="where to save them (default: <save-dir>/val_previews; implies --save-val-image)")
    a = ap.parse_args()

    save_dir = a.save_dir or HERE / ("checkpoints_light_mono" if a.monochrome else "checkpoints_light")
    save_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = a.val_preview_dir or save_dir / "val_previews"
    previews = a.save_val_image or a.val_preview_dir is not None
    device = torch.device("cuda")
    ds_args = dict(patch_size=a.patch_size, monochrome=a.monochrome, noise_gain=tuple(a.noise_gain), blur=tuple(a.blur),
                   target_blur=a.target_blur, mix_prob=a.mix_prob, partial_denoise=a.partial_denoise)
    train_ds = BayerPatchDataset(a.patches_dir / "train", is_train=True, **ds_args)
    val_ds = BayerPatchDataset(a.patches_dir / "val", is_train=False, **ds_args)
    train_dl = DataLoader(train_ds, batch_size=a.batch_size, shuffle=True, num_workers=a.workers, pin_memory=True,
                          drop_last=True, persistent_workers=True)
    val_dl = DataLoader(val_ds, batch_size=a.batch_size, shuffle=False, num_workers=2)

    model = LightBayerSR(a.arch, a.channels, 1 if a.monochrome else 3)
    if a.init:
        model.load_state_dict(torch.load(a.init, map_location="cpu", weights_only=True)["state_dict"])
    model.to(device)
    print(f"{a.arch}-{a.channels}, {'black and white' if a.monochrome else 'colour'}: "
          f"{sum(p.numel() for p in model.parameters()) / 1e6:.2f} M parameters (training form), "
          f"about {seconds_per_frame(model):.1f} s per 20 MP frame | {len(train_ds)} train, {len(val_ds)} val patches")
    model.train()

    teacher = None
    if a.teacher:
        from riffle import rawsr

        teacher, kind, conditioned = rawsr.load(a.teacher)
        if kind != ("mono" if a.monochrome else "rgb") or not conditioned:
            sys.exit(f"the teacher must be a {'mono' if a.monochrome else 'rgb'} checkpoint with the noise input")
        teacher.eval()
        print(f"teacher: {a.teacher} (distill weight {a.distill_weight})")

    optimizer = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    total = a.epochs * len(train_dl)
    warmup = max(1, int(total * a.warmup_pct))
    scheduler = torch.optim.lr_scheduler.SequentialLR(optimizer, [
        torch.optim.lr_scheduler.LinearLR(optimizer, start_factor=0.01, total_iters=warmup),
        torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, total - warmup), eta_min=a.min_lr),
    ], milestones=[warmup])
    scaler = torch.amp.GradScaler("cuda")
    edge = EdgeLoss(channels=1 if a.monochrome else 3).to(device)
    best = -1.0

    for epoch in range(a.epochs):
        model.train()
        running = 0.0
        bar = tqdm(train_dl, desc=f"epoch {epoch + 1}/{a.epochs}", leave=False)
        for x, y, n, _ in bar:
            x, y, n = x.to(device, non_blocking=True), y.to(device, non_blocking=True), n.to(device)
            target_t = None
            if teacher is not None:
                with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
                    target_t = teacher(x, n).float().clamp(0, 1)
            with torch.autocast("cuda", dtype=torch.float16):
                out = model(x, n)
                loss = F.l1_loss(out, y) + a.edge_weight * edge(out, y)
                if target_t is not None:
                    loss = loss + a.distill_weight * F.l1_loss(out, target_t)
            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()
            running += loss.item()
            bar.set_postfix(loss=f"{loss.item():.4f}", lr=f"{scheduler.get_last_lr()[0]:.1e}")

        model.eval()
        scores = []
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            for i_val, (x, y, n, _) in enumerate(val_dl):
                out = model(x.to(device), n.to(device))
                scores += [psnr(o[None], t[None]) for o, t in zip(out, y.to(device))]
                if i_val == 0 and previews:
                    save_val_preview(x, y, out.float().clamp(0, 1), preview_dir / f"val_epoch_{epoch + 1:03d}.png",
                                     epoch + 1, is_mono=a.monochrome)
        val = sum(scores) / len(scores)
        torch.save(model.checkpoint(), save_dir / "latest.pt")
        note = ""
        if val > best:
            best = val
            torch.save(model.checkpoint(), save_dir / "best_psnr.pt")
            note = "best"
        print(f"epoch {epoch + 1:3d}: train loss {running / len(train_dl):.4f}, val PSNR {val:.2f} dB {note}")


if __name__ == "__main__":
    main()
