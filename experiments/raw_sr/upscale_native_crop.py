import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import time
import numpy as np
import cv2
import rawpy
import torch
from PIL import Image, ImageDraw, ImageFont
from transformers import Swin2SRForImageSuperResolution

from model import BayerSwin2SR
from raw_to_dng import write_dng, append_exif, RawTiff, EXIF_TAGS, tiled
from compare import render, load

GAMMA = 2.2

def run_native_crop_experiment(raw_path: Path, out_dir: Path, crop_center=(0.57, 0.60), crop_size=600):
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading {raw_path.name}...")
    tiff = RawTiff(raw_path)
    make = tiff.text(271)
    model_name = tiff.text(272)
    exif = {c: v for c, v in tiff.exif.items() if c in EXIF_TAGS}

    with rawpy.imread(str(raw_path)) as raw:
        wb = np.array(raw.camera_whitebalance[:3], np.float64)
        if not wb.any(): wb = np.array(raw.daylight_whitebalance[:3], np.float64)
        wb = wb / wb[1]
        cam_xyz = np.array(raw.rgb_xyz_matrix[:3], np.float64)
        flip = raw.sizes.flip
        info = {"wb": wb, "cam_xyz": cam_xyz, "flip": 0}

        raw_img = raw.raw_image.astype(np.float32)
        b = np.array(raw.black_level_per_channel, dtype=np.float32)
        w = float(raw.white_level)

        H, W = raw_img.shape
        cx = int(crop_center[0] * W)
        cy = int(crop_center[1] * H)
        cx, cy = cx - (cx % 2), cy - (cy % 2)
        
        half_s = crop_size // 2
        half_s = half_s - (half_s % 2)
        x0, y0 = cx - half_s, cy - half_s
        x0, y0 = max(0, x0 - (x0 % 2)), max(0, y0 - (y0 % 2))
        x1, y1 = x0 + crop_size, y0 + crop_size
        print(f"Crop coordinates in native RAW: x=[{x0}:{x1}], y=[{y0}:{y1}] (size {crop_size}x{crop_size})")

        # 1. Native Bayer quads (NO binning, NO skipping quads!)
        r = np.clip((raw_img[y0:y1:2, x0:x1:2] - b[0]) / (w - b[0]), 0, 1)
        g1 = np.clip((raw_img[y0:y1:2, x0+1:x1:2] - b[1]) / (w - b[1]), 0, 1)
        g2 = np.clip((raw_img[y0+1:y1:2, x0:x1:2] - b[3]) / (w - b[3]), 0, 1)
        b_ch = np.clip((raw_img[y0+1:y1:2, x0+1:x1:2] - b[2]) / (w - b[2]), 0, 1)
        packed_crop = np.stack([r, g1, g2, b_ch], axis=0) # (4, crop_size/2, crop_size/2)

        # 2. Native demosaiced full image for baseline comparison
        gt_rgb_full = raw.postprocess(
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
            use_camera_wb=False, use_auto_wb=False, user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw, gamma=(1, 1), no_auto_bright=True,
            output_bps=16, highlight_mode=rawpy.HighlightMode.Clip, user_flip=0,
        ).astype(np.float32) / 65535.0
        gt_crop = gt_rgb_full[y0:y1, x0:x1, :] # (crop_size, crop_size, 3)

    # -------------------------------------------------------------
    # Pipeline A: Our Model (BayerSwin2SR) directly from Native Bayer Crop
    # -------------------------------------------------------------
    print("Upscaling native Bayer crop with BayerSwin2SR (2x upscale, 4x from packed quads)...")
    wb_bayer = np.array([wb[0], wb[1], wb[1], wb[2]], dtype=np.float32).reshape(4, 1, 1)
    input_wb = packed_crop * wb_bayer
    head = float(max(1.0, input_wb.max()))
    input_disp = np.power(np.clip(input_wb / head, 0, 1), 1 / GAMMA).astype(np.float32)

    net = BayerSwin2SR().cuda().eval()
    ckpt = Path("experiments/raw_sr/checkpoints/best_model.pt")
    net.load_checkpoint(ckpt)

    disp_hwc = np.moveaxis(input_disp, 0, -1) # (crop_size/2, crop_size/2, 4)
    def run_bayer(t):
        return net(t).clamp(0, 1)

    bayer_out_disp = tiled(disp_hwc, 4, run_bayer, multiple=8) # (crop_size*2, crop_size*2, 3)
    bayer_lin = np.power(np.clip(bayer_out_disp, 0, 1), GAMMA) * head / wb[None, None, :]

    out_bayer_dng = out_dir / "crop_bayer_upscale.dng"
    write_dng(out_bayer_dng, bayer_lin, info, make, model_name)
    append_exif(out_bayer_dng, exif, tiff.order)
    print(f"Saved {out_bayer_dng}")

    # -------------------------------------------------------------
    # Pipeline B: Traditional RGB Baseline (Demosaic -> Swin2SR x2)
    # -------------------------------------------------------------
    print("Upscaling native demosaiced crop with Swin2SR x2...")
    gt_wb = gt_crop * wb[None, None, :]
    gt_head = float(max(1.0, gt_wb.max()))
    gt_disp = np.power(np.clip(gt_wb / gt_head, 0, 1), 1 / GAMMA).astype(np.float32)

    baseline_model = Swin2SRForImageSuperResolution.from_pretrained('caidas/swin2SR-classical-sr-x2-64').cuda().eval()
    def run_rgb(t):
        return baseline_model(t).reconstruction.clamp(0, 1)

    rgb_out_disp = tiled(gt_disp, 2, run_rgb, multiple=8) # (crop_size*2, crop_size*2, 3)
    rgb_lin = np.power(np.clip(rgb_out_disp, 0, 1), GAMMA) * gt_head / wb[None, None, :]

    out_rgb_dng = out_dir / "crop_rgb_upscale.dng"
    write_dng(out_rgb_dng, rgb_lin, info, make, model_name)
    append_exif(out_rgb_dng, exif, tiff.order)
    print(f"Saved {out_rgb_dng}")

    # -------------------------------------------------------------
    # Pipeline C: Native 1x Reference (Demosaiced native crop)
    # -------------------------------------------------------------
    out_ref_dng = out_dir / "crop_native_ref.dng"
    write_dng(out_ref_dng, gt_crop, info, make, model_name)
    append_exif(out_ref_dng, exif, tiff.order)
    print(f"Saved {out_ref_dng}")

    # -------------------------------------------------------------
    # Render all 3 DNGs with Darktable for consistent comparison
    # -------------------------------------------------------------
    print("\nRendering DNGs with Darktable...")
    render(out_ref_dng, out_dir / "crop_native_ref.tif")
    render(out_rgb_dng, out_dir / "crop_rgb_upscale.tif")
    render(out_bayer_dng, out_dir / "crop_bayer_upscale.tif")

    im_ref = load(out_dir / "crop_native_ref.tif") # (crop_size, crop_size, 3)
    im_rgb = load(out_dir / "crop_rgb_upscale.tif") # (crop_size*2, crop_size*2, 3)
    im_bayer = load(out_dir / "crop_bayer_upscale.tif") # (crop_size*2, crop_size*2, 3)

    # Also make a 2x Lanczos version of the native RAW for direct 1:1 pixel comparison
    im_ref_2x = cv2.resize(im_ref, (im_bayer.shape[1], im_bayer.shape[0]), interpolation=cv2.INTER_LANCZOS4)

    # -------------------------------------------------------------
    # Generate 4-panel comparison crop:
    # 1. Native RAW (1x scaled 2x Lanczos for display)
    # 2. Native RAW demosaiced -> Swin2SR x2
    # 3. Native Bayer -> BayerSwin2SR (Our Model)
    # -------------------------------------------------------------
    print("Creating side-by-side comparison image...")
    # Center crop of the 2x images for close-up inspection (e.g. 500x500 of the head/eye)
    H2, W2 = im_bayer.shape[:2]
    cx2, cy2 = W2 // 2, H2 // 2
    box = 250 # 500x500 box in 2x space
    
    c_ref_2x = im_ref_2x[cy2-box:cy2+box, cx2-box:cx2+box]
    c_rgb = im_rgb[cy2-box:cy2+box, cx2-box:cx2+box]
    c_bayer = im_bayer[cy2-box:cy2+box, cx2-box:cx2+box]

    panels = [
        ("Native RAW 1x (Lanczos 2x display)", (np.clip(c_ref_2x, 0, 1) * 255).astype(np.uint8)),
        ("Demosaic -> Swin2SR x2 (RGB Baseline)", (np.clip(c_rgb, 0, 1) * 255).astype(np.uint8)),
        ("Pure Native Bayer -> BayerSwin2SR (Our Model)", (np.clip(c_bayer, 0, 1) * 255).astype(np.uint8)),
    ]

    h_p, w_p = panels[0][1].shape[:2]
    header = 30
    strip = Image.new("RGB", (w_p * len(panels) + (len(panels) - 1) * 4, h_p + header), (30, 30, 30))
    draw = ImageDraw.Draw(strip)

    for i, (name, arr) in enumerate(panels):
        im = Image.fromarray(arr)
        x_off = i * (w_p + 4)
        strip.paste(im, (x_off, header))
        draw.text((x_off + 8, 8), name, fill=(240, 240, 240))

    out_strip = out_dir / "crop_comparison_native_raw.png"
    strip.save(out_strip)
    print(f"\nSaved comparison image to: {out_strip}")

if __name__ == "__main__":
    run_native_crop_experiment(
        raw_path=Path("experiments/raw_sr/raws/P5290599.ORF"),
        out_dir=Path("experiments/raw_sr/compare_native_crop"),
        crop_center=(0.57, 0.60),
        crop_size=600
    )
