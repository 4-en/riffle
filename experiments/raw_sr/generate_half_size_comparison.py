import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import time
import struct
import numpy as np
import cv2
import rawpy
import torch
import torch.nn.functional as F
from transformers import Swin2SRForImageSuperResolution

from model import BayerSwin2SR
from raw_to_dng import write_dng, append_exif, RawTiff, EXIF_TAGS, tiled

GAMMA = 2.2

def generate_half_size_dngs(raw_path: Path):
    print(f"Loading {raw_path.name}...")
    tiff = RawTiff(raw_path)
    
    with rawpy.imread(str(raw_path)) as raw:
        wb = np.array(raw.camera_whitebalance[:3], np.float64)
        if not wb.any(): wb = np.array(raw.daylight_whitebalance[:3], np.float64)
        wb = wb / wb[1]
        cam_xyz = np.array(raw.rgb_xyz_matrix[:3], np.float64)
        flip = raw.sizes.flip
        info = {"wb": wb, "cam_xyz": cam_xyz, "flip": flip}
        
        # 1. Native Full Raw Image
        raw_img = raw.raw_image.astype(np.float32)
        b = np.array(raw.black_level_per_channel, dtype=np.float32)
        w = float(raw.white_level)
        
        r = np.clip((raw_img[0::2, 0::2] - b[0]) / (w - b[0]), 0, 1)
        g1 = np.clip((raw_img[0::2, 1::2] - b[1]) / (w - b[1]), 0, 1)
        g2 = np.clip((raw_img[1::2, 0::2] - b[3]) / (w - b[3]), 0, 1)
        b_ch = np.clip((raw_img[1::2, 1::2] - b[2]) / (w - b[2]), 0, 1)
        
        # Packed native: (4, H/2, W/2)
        packed_native = np.stack([r, g1, g2, b_ch], axis=0)
        
        # Full data linear demosaiced RGB for the RGB baseline
        gt_rgb = raw.postprocess(
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
            use_camera_wb=False, use_auto_wb=False, user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw, gamma=(1, 1), no_auto_bright=True,
            output_bps=16, highlight_mode=rawpy.HighlightMode.Clip, user_flip=0,
        ).astype(np.float32) / 65535.0

    H, W = gt_rgb.shape[:2]
    make = tiff.text(271)
    model_name = tiff.text(272)
    exif = {c: v for c, v in tiff.exif.items() if c in EXIF_TAGS}
    
    # -------------------------------------------------------------
    # 1. Bayer Model: Quartet Binning (1/4 data) -> 4x upscale to Native
    # -------------------------------------------------------------
    print("Generating My Model DNG from Half-Size Bayer...")
    bp_tensor = torch.from_numpy(packed_native).unsqueeze(0) # (1, 4, H/2, W/2)
    binned = F.avg_pool2d(bp_tensor, kernel_size=2, stride=2).squeeze(0).numpy() # (4, H/4, W/4)
    
    # Apply WB and Gamma matching training
    wb_bayer = np.array([wb[0], wb[1], wb[1], wb[2]], dtype=np.float32).reshape(4, 1, 1)
    input_wb = binned * wb_bayer
    head = float(max(1.0, input_wb.max()))
    input_disp = np.power(np.clip(input_wb / head, 0, 1), 1 / GAMMA).astype(np.float32) # (4, H/4, W/4)
    
    # Run through trained BayerSwin2SR
    net = BayerSwin2SR().cuda().eval()
    ckpt = Path("experiments/raw_sr/checkpoints/best_model.pt")
    net.load_checkpoint(ckpt)
    
    # tiled expects (H_in, W_in, C)
    disp_hwc = np.moveaxis(input_disp, 0, -1) # (H/4, W/4, 4)
    def run_bayer(t):
        return net(t).clamp(0, 1)
    
    bayer_out_disp = tiled(disp_hwc, 4, run_bayer, multiple=8) # (H, W, 3)
    bayer_lin = np.power(np.clip(bayer_out_disp, 0, 1), GAMMA) * head / wb[None, None, :]
    
    # Save DNG
    out_bayer_dng = Path("experiments/raw_sr/half_bayer_upscale.dng")
    write_dng(out_bayer_dng, bayer_lin, info, make, model_name)
    append_exif(out_bayer_dng, exif, tiff.order)
    print(f"Saved {out_bayer_dng}")

    # -------------------------------------------------------------
    # 2. RGB Baseline: Half-size Bayer -> Demosaic (LibRaw AHD) -> Swin2SR x2
    # -------------------------------------------------------------
    print("Generating Baseline RGB DNG from Half-Size Bayer demosaiced with LibRaw AHD...")
    import tifffile
    half_H, half_W = binned.shape[1] * 2, binned.shape[2] * 2
    half_cfa_norm = np.empty((half_H, half_W), dtype=np.float32)
    half_cfa_norm[0::2, 0::2] = binned[0]
    half_cfa_norm[0::2, 1::2] = binned[1]
    half_cfa_norm[1::2, 0::2] = binned[2]
    half_cfa_norm[1::2, 1::2] = binned[3]

    cfa_uint16 = (np.clip(half_cfa_norm, 0, 1) * 65535 + 0.5).astype(np.uint16)
    cfa_tags = [
        (50706, 'B', 4, (1, 4, 0, 0), True),
        (50707, 'B', 4, (1, 1, 0, 0), True),
        (50713, 'H', 2, (2, 2), True),
        (33422, 'B', 4, (0, 1, 1, 2), True), # RGGB
        (50714, 'I', 1, 0, True),
        (50717, 'I', 1, 65535, True),
    ]
    temp_cfa_dng = Path("experiments/raw_sr/temp_half_cfa.dng")
    tifffile.imwrite(temp_cfa_dng, cfa_uint16, photometric=32803, planarconfig='contig', extratags=cfa_tags)

    with rawpy.imread(str(temp_cfa_dng)) as r:
        half_rgb = r.postprocess(
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
            use_camera_wb=False, use_auto_wb=False, user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw, gamma=(1, 1), no_auto_bright=True,
            output_bps=16, highlight_mode=rawpy.HighlightMode.Clip, user_flip=0,
        ).astype(np.float32) / 65535.0
    if temp_cfa_dng.exists():
        temp_cfa_dng.unlink()

    # Target display space for Swin2SR
    half_rgb_wb = half_rgb * wb[None, None, :]
    half_head = float(max(1.0, half_rgb_wb.max()))
    half_rgb_disp = np.power(np.clip(half_rgb_wb / half_head, 0, 1), 1 / GAMMA).astype(np.float32)
    
    baseline_model = Swin2SRForImageSuperResolution.from_pretrained('caidas/swin2SR-classical-sr-x2-64').cuda().eval()
    def run_rgb(t):
        # t is (B, 3, h, w)
        return baseline_model(t).reconstruction.clamp(0, 1)
        
    rgb_out_disp = tiled(half_rgb_disp, 2, run_rgb, multiple=8) # (H, W, 3)
    rgb_lin = np.power(np.clip(rgb_out_disp, 0, 1), GAMMA) * half_head / wb[None, None, :]
    
    out_rgb_dng = Path("experiments/raw_sr/half_rgb_upscale.dng")
    write_dng(out_rgb_dng, rgb_lin, info, make, model_name)
    append_exif(out_rgb_dng, exif, tiff.order)
    print(f"Saved {out_rgb_dng}")
    
    print("\nBoth half-size upscaled DNGs ready for compare.py!")

if __name__ == "__main__":
    generate_half_size_dngs(Path("experiments/raw_sr/raws/P5290599.ORF"))
