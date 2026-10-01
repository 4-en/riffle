import cv2
import numpy as np
import torch
import rawpy
from pathlib import Path
from transformers import Swin2SRForImageSuperResolution

from model import BayerSwin2SR

GAMMA = 2.2

def psnr(img1, img2):
    mse = np.mean((img1 - img2) ** 2)
    if mse == 0: return 100
    return -10 * np.log10(mse + 1e-8)

def tiled_forward(tensor, model, tile_size=256, overlap=32, scale=2, is_hf=False):
    B, C, H, W = tensor.shape
    out = torch.zeros((B, 3, H * scale, W * scale), device=tensor.device)
    count = torch.zeros((B, 3, H * scale, W * scale), device=tensor.device)
    
    stride = tile_size - overlap
    for y in range(0, H, stride):
        for x in range(0, W, stride):
            y2 = min(y + tile_size, H)
            x2 = min(x + tile_size, W)
            y1 = max(0, y2 - tile_size)
            x1 = max(0, x2 - tile_size)
            
            patch = tensor[:, :, y1:y2, x1:x2]
            with torch.no_grad(), torch.amp.autocast('cuda'):
                pred = model(patch)
                if is_hf:
                    pred = pred.reconstruction
            
            oy1, oy2 = y1 * scale, y2 * scale
            ox1, ox2 = x1 * scale, x2 * scale
            out[:, :, oy1:oy2, ox1:ox2] += pred
            count[:, :, oy1:oy2, ox1:ox2] += 1
            
    return out / count

def get_bayer_input(raw_path):
    with rawpy.imread(str(raw_path)) as raw:
        wb = np.array(raw.camera_whitebalance[:3], np.float64)
        if not wb.any(): wb = np.array(raw.daylight_whitebalance[:3], np.float64)
        wb = wb / wb[1]
        
        raw_img = raw.raw_image.astype(np.float32)
        b = np.array(raw.black_level_per_channel, dtype=np.float32)
        w = float(raw.white_level)
        r = np.clip((raw_img[0::2, 0::2] - b[0]) / (w - b[0]), 0, 1)
        g1 = np.clip((raw_img[0::2, 1::2] - b[1]) / (w - b[1]), 0, 1)
        g2 = np.clip((raw_img[1::2, 0::2] - b[3]) / (w - b[3]), 0, 1)
        b_ch = np.clip((raw_img[1::2, 1::2] - b[2]) / (w - b[2]), 0, 1)
        
        packed = np.stack([r, g1, g2, b_ch], axis=0) # (4, H/2, W/2)
        
        # Quartet binning (1/4 data)
        bp_tensor = torch.from_numpy(packed).unsqueeze(0) # (1, 4, H/2, W/2)
        binned = torch.nn.functional.avg_pool2d(bp_tensor, kernel_size=2, stride=2).squeeze(0).numpy() # (4, H/4, W/4)
        
        # WB and Gamma
        wb_bayer = np.array([wb[0], wb[1], wb[1], wb[2]], dtype=np.float32).reshape(4, 1, 1)
        input_wb = binned * wb_bayer
        head = float(max(1.0, input_wb.max()))
        input_disp = np.power(np.clip(input_wb / head, 0, 1), 1 / GAMMA).astype(np.float32)
        
        return torch.from_numpy(input_disp).unsqueeze(0), wb, head

def main():
    raw_path = Path("experiments/raw_sr/raws/P5290599.ORF")
    print("1. Loading Ground Truth (LibRaw AHD Full Data)")
    with rawpy.imread(str(raw_path)) as raw:
        gt_rgb = raw.postprocess(
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
            use_camera_wb=False, use_auto_wb=False, user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw, gamma=(1, 1), no_auto_bright=True,
            output_bps=16, highlight_mode=rawpy.HighlightMode.Clip, user_flip=0,
        ).astype(np.float32) / 65535.0
        
        wb = np.array(raw.camera_whitebalance[:3], np.float64)
        if not wb.any(): wb = np.array(raw.daylight_whitebalance[:3], np.float64)
        wb = wb / wb[1]
        
    gt_wb = gt_rgb * wb[None, None, :]
    head = float(max(1.0, gt_wb.max()))
    dt_rgb = np.power(np.clip(gt_wb / head, 0, 1), 1 / GAMMA).astype(np.float32)
    
    print("2. Generating Baseline Upscale (from 1/4 Data)")
    H, W = dt_rgb.shape[:2]
    # Downscale to 1/4 data (H/2 x W/2)
    dt_lowres = cv2.resize(dt_rgb, (W//2, H//2), interpolation=cv2.INTER_LANCZOS4)
    
    baseline_model = Swin2SRForImageSuperResolution.from_pretrained('caidas/swin2SR-classical-sr-x2-64').cuda().eval()
    t_in = torch.from_numpy(dt_lowres).permute(2, 0, 1).unsqueeze(0).cuda()
    with torch.no_grad(), torch.amp.autocast('cuda'):
        base_out = tiled_forward(t_in, baseline_model, tile_size=256, overlap=32, scale=2, is_hf=True)
    base_rgb = base_out.squeeze(0).permute(1, 2, 0).cpu().numpy().clip(0, 1)
    base_rgb = cv2.resize(base_rgb, (W, H), interpolation=cv2.INTER_LANCZOS4)
    
    print("3. Generating Bayer Upscale (from 1/4 Data)")
    bayer_model = BayerSwin2SR().cuda().eval()
    bayer_model.load_checkpoint("experiments/raw_sr/checkpoints/best_model.pt")
    
    bayer_in, _, _ = get_bayer_input(raw_path)
    bayer_in = bayer_in.cuda()
    
    bayer_out = tiled_forward(bayer_in, bayer_model, tile_size=256, overlap=32, scale=4, is_hf=False)
    bayer_rgb = bayer_out.squeeze(0).permute(1, 2, 0).cpu().numpy().clip(0, 1)
    
    print("\n--- Full Image Metrics ---")
    psnr_base = psnr(dt_rgb, base_rgb)
    psnr_bayer = psnr(dt_rgb, bayer_rgb)
    
    print(f"PSNR (Baseline vs GT): {psnr_base:.2f} dB")
    print(f"PSNR (Bayer vs GT): {psnr_bayer:.2f} dB")
    
    print("4. Extracting Full Image Crops")
    out_dir = Path("experiments/raw_sr/full_eval_crops")
    out_dir.mkdir(exist_ok=True)
    
    centers = [(W//2, H//2), (W//4, H//4), (3*W//4, 3*H//4)]
    size = 400
    for i, (cx, cy) in enumerate(centers):
        c_dt = dt_rgb[cy:cy+size, cx:cx+size]
        c_base = base_rgb[cy:cy+size, cx:cx+size]
        c_bayer = bayer_rgb[cy:cy+size, cx:cx+size]
        
        combined = np.concatenate([c_dt, c_base, c_bayer], axis=1)
        combined = cv2.cvtColor((combined * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        
        font = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(combined, "LibRaw GT (Full Data)", (10, 30), font, 0.7, (255,255,255), 2)
        cv2.putText(combined, "Baseline (1/4 Data)", (size + 10, 30), font, 0.7, (255,255,255), 2)
        cv2.putText(combined, "Bayer Model (1/4 Data)", (2*size + 10, 30), font, 0.7, (255,255,255), 2)
        
        cv2.imwrite(str(out_dir / f"crop_{i}.jpg"), combined)

if __name__ == "__main__":
    main()
