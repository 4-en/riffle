import cv2
import numpy as np
from pathlib import Path

def laplacian_var(img_gray):
    # Laplacian variance measures total high-frequency edge energy
    lap = cv2.Laplacian(img_gray, cv2.CV_64F)
    return lap.var()

def sobel_magnitude(img_gray):
    gx = cv2.Sobel(img_gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(img_gray, cv2.CV_64F, 0, 1, ksize=3)
    return np.sqrt(gx**2 + gy**2)

def edge_psnr(pred_gray, gt_gray):
    mag_pred = sobel_magnitude(pred_gray)
    mag_gt = sobel_magnitude(gt_gray)
    mse = np.mean((mag_pred - mag_gt) ** 2)
    if mse == 0:
        return 100.0
    data_range = max(mag_gt.max(), 1.0)
    return 20 * np.log10(data_range) - 10 * np.log10(mse)

def standard_psnr(pred, gt):
    mse = np.mean((pred.astype(np.float64) - gt.astype(np.float64)) ** 2)
    if mse == 0:
        return 100.0
    return 20 * np.log10(255.0) - 10 * np.log10(mse)

def main():
    crops_dir = Path("experiments/raw_sr/full_eval_crops")
    crop_files = sorted(crops_dir.glob("crop_*.jpg"))
    if not crop_files:
        print("No crops found in full_eval_crops/")
        return

    print("=" * 70)
    print(f"{'Crop':<8} | {'Metric':<18} | {'Ground Truth':<12} | {'Baseline':<12} | {'Bayer Model':<12}")
    print("=" * 70)

    for f in crop_files:
        img = cv2.imread(str(f))
        H, W = img.shape[:2]
        size = W // 3

        # Exclude top 45 pixels to avoid the text annotations
        gt = img[45:, 0:size]
        base = img[45:, size:2*size]
        bayer = img[45:, 2*size:3*size]

        gt_g = cv2.cvtColor(gt, cv2.COLOR_BGR2GRAY) / 255.0
        base_g = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY) / 255.0
        bayer_g = cv2.cvtColor(bayer, cv2.COLOR_BGR2GRAY) / 255.0

        # 1. Laplacian Variance
        var_gt = laplacian_var(gt_g)
        var_base = laplacian_var(base_g)
        var_bayer = laplacian_var(bayer_g)

        # 2. Sharpness Ratio (relative to GT = 1.0)
        s_ratio_base = var_base / (var_gt + 1e-8)
        s_ratio_bayer = var_bayer / (var_gt + 1e-8)

        # 3. Standard PSNR vs GT
        psnr_base = standard_psnr(base, gt)
        psnr_bayer = standard_psnr(bayer, gt)

        # 4. Edge PSNR vs GT
        e_psnr_base = edge_psnr(base_g, gt_g)
        e_psnr_bayer = edge_psnr(bayer_g, gt_g)

        print(f"{f.stem:<8} | {'Std PSNR':<18} | {'(ref)':<12} | {psnr_base:>8.2f} dB   | {psnr_bayer:>8.2f} dB")
        print(f"{'':<8} | {'Edge PSNR':<18} | {'(ref)':<12} | {e_psnr_base:>8.2f} dB   | {e_psnr_bayer:>8.2f} dB")
        print(f"{'':<8} | {'Sharpness Ratio':<18} | {'1.000':<12} | {s_ratio_base:>8.3f}      | {s_ratio_bayer:>8.3f}")
        print("-" * 70)

if __name__ == "__main__":
    main()
