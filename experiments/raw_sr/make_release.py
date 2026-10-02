"""Builds the Hugging Face release of the Bayer models in huggingface/.

    venv/bin/python experiments/raw_sr/make_release.py [--rgb PT] [--mono PT] [--out DIR]

- Weights: the checkpoints as safetensors (checked to give the same output as the .pt).
- Code: src/riffle/rawsr.py, copied (it runs on its own), beside upscale.py and requirements.txt.
- Evaluation on the held-out patches (data_binned/val, photos never trained on), with ground
  truth: the input as one real pixel's noise at the photo's ISO (noise gain 1), blurred by
  sigma 1.25-3.1 (the range measured on real ORFs), the target as in training. Compared:
  the input demosaiced (OpenCV, edge-aware) and enlarged bicubic; Real-ESRGAN ×2 and Swin2SR
  ×2 on that demosaic; the Riffle models. PSNR and SSIM, colour and black and white (MONO_MIX
  of linear RGB; for the RGB methods converted the same way).
- Images: grids of held-out patches, and crops of held-out RAWs (no ground truth: the real
  input) rendered by darktable from DNGs, so every method is developed the same way.
- The model card: model_card.md with the tables and images filled in.

Photos with people in them are used for the numbers only, never for the images.
"""

import argparse
import csv
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

from compare import render  # noqa: E402  darktable-cli with a throwaway config
from dataset import BayerPatchDataset  # noqa: E402
from riffle import rawsr  # noqa: E402

GAMMA = rawsr.GAMMA
MIX = rawsr.MONO_MIX.astype(np.float32)
NO_IMAGES = {"P5290481", "P5280148", "P5290370"}  # people in them: numbers only
# Held-out RAWs for the real crops: (file stem, centre x, y as fractions of the upright frame, label)
REAL_CROPS = [
    ("P3290093", 0.524, 0.56, "duck"),
    ("P7130047", 0.356, 0.37, "owl"),
    ("P5280259", 0.50, 0.45, "palace"),
    ("P7091148", 0.39, 0.27, "flower"),
]
CROP = 256  # native px of a real crop (512 px upscaled)
MARGIN = 16  # packed px of context around it, trimmed after


# ---- helpers -----------------------------------------------------------------------------


def to_gamma(lin):
    return np.power(np.clip(lin, 0, 1), 1 / GAMMA)


def to_lin(g):
    return np.power(np.clip(g, 0, 1), GAMMA)


def mono(rgb_gamma):
    """(H, W, 3) gamma → (H, W) gamma: MONO_MIX of linear RGB, as the black-and-white model."""
    return to_gamma(to_lin(rgb_gamma) @ MIX)


def psnr(a, b, border=8):
    a, b = a[border:-border, border:-border], b[border:-border, border:-border]
    mse = float(np.mean((a.astype(np.float64) - b) ** 2))
    return 10 * math.log10(1 / max(mse, 1e-12))


def ssim(a, b, border=8):
    """SSIM (Gaussian window 11, sigma 1.5, data range 1), averaged over channels."""
    a, b = a[border:-border, border:-border], b[border:-border, border:-border]
    if a.ndim == 2:
        a, b = a[..., None], b[..., None]
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)
    out = []
    for c in range(a.shape[2]):
        x, y = a[..., c].astype(np.float64), b[..., c].astype(np.float64)
        mx, my = blur(x), blur(y)
        sx, sy, sxy = blur(x * x) - mx * mx, blur(y * y) - my * my, blur(x * y) - mx * my
        m = ((2 * mx * my + c1) * (2 * sxy + c2)) / ((mx * mx + my * my + c1) * (sx + sy + c2))
        out.append(m[5:-5, 5:-5].mean())
    return float(np.mean(out))


def demosaic(packed_lin):
    """(4, h, w) packed RGGB, linear → (2h, 2w, 3) linear RGB (OpenCV, edge-aware)."""
    _, h, w = packed_lin.shape
    m = np.zeros((2 * h, 2 * w), np.float32)
    m[0::2, 0::2], m[0::2, 1::2], m[1::2, 0::2], m[1::2, 1::2] = packed_lin
    u16 = (np.clip(m, 0, 1) * 65535 + 0.5).astype(np.uint16)
    return cv2.cvtColor(u16, cv2.COLOR_BayerBG2RGB_EA).astype(np.float32) / 65535  # BG: OpenCV's name for RGGB


class Baselines:
    """Real-ESRGAN ×2 (x2plus) and Swin2SR classical ×2, on gamma RGB in 0-1."""

    def __init__(self):
        import spandrel
        from huggingface_hub import hf_hub_download
        from transformers import Swin2SRForImageSuperResolution

        path = hf_hub_download("deAPI-ai/realesrgan-x2", "RealESRGAN_x2plus.pth")
        self.esrgan = spandrel.ModelLoader().load_from_file(path).cuda().eval()
        self.swin = Swin2SRForImageSuperResolution.from_pretrained("caidas/swin2SR-classical-sr-x2-64").cuda().eval()

    @torch.inference_mode()
    def run(self, rgb, which):
        h, w = rgb.shape[:2]
        ph, pw = (-h) % 8, (-w) % 8
        x = np.pad(rgb, ((0, ph), (0, pw), (0, 0)), mode="reflect")
        t = torch.from_numpy(np.ascontiguousarray(x)).permute(2, 0, 1)[None].cuda()
        out = self.esrgan(t) if which == "esrgan" else self.swin(pixel_values=t).reconstruction
        return out[0].clamp(0, 1).permute(1, 2, 0).cpu().numpy()[: 2 * h, : 2 * w]


def label_grid(columns, labels, title=None, scale=1):
    """Images side by side (gamma, 0-1; 2-D for grey) with a label above each (the default
    font has no "×", hence "x2")."""
    font = ImageFont.load_default(size=15)
    tiles = []
    for im in columns:
        im = np.repeat(im[..., None], 3, axis=2) if im.ndim == 2 else im
        u8 = (np.clip(im, 0, 1) * 255 + 0.5).astype(np.uint8)
        if scale != 1:
            u8 = cv2.resize(u8, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        tiles.append(Image.fromarray(u8))
    w, h, gap, head = tiles[0].width, tiles[0].height, 6, 24 + (22 if title else 0)
    sheet = Image.new("RGB", (len(tiles) * w + (len(tiles) - 1) * gap, h + head), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    if title:
        draw.text((4, 3), title, fill=(200, 200, 200), font=font)
    for i, (t, name) in enumerate(zip(tiles, labels)):
        x = i * (w + gap)
        sheet.paste(t, (x, head))
        draw.text((x + 4, head - 21), name, fill=(240, 240, 240), font=font)
    return sheet


# ---- the release -------------------------------------------------------------------------


def export_weights(rgb_pt, mono_pt, out):
    from safetensors.torch import save_file

    names = {}
    for kind, pt in (("rgb", rgb_pt), ("mono", mono_pt)):
        state = torch.load(pt, map_location="cpu", weights_only=True)
        name = f"riffle-raw-sr-{kind}.safetensors"
        save_file({k: v.contiguous() for k, v in state.items()}, out / name, metadata={"kind": kind, "format": "pt"})
        rawsr._models.clear()
        a, k_a, _ = rawsr.load(str(pt))
        x = torch.rand(1, 4, 40, 48, device=rawsr._device())
        n = torch.tensor([0.0], device=x.device)
        with torch.no_grad():
            ref = a(x, n)
        rawsr._models.clear()
        b, k_b, _ = rawsr.load(str(out / name))
        with torch.no_grad():
            same = torch.equal(ref, b(x, n))
        assert k_a == k_b == kind and same, f"{name} does not reproduce {pt}"
        names[kind] = name
        print(f"{name}: from {pt}, same output")
    rawsr._models.clear()
    return names


def evaluate(weights, out, base):
    """Metrics on every held-out patch; returns (rows, the patches kept for images)."""
    ds = BayerPatchDataset(HERE / "data_binned" / "val", patch_size=256, is_train=False, noise_gain=(1.0, 1.0),
                           blur=(1.25, 3.1), target_blur=0.5, partial_denoise=0.0)
    m_rgb, _, _ = rawsr.load(str(out / weights["rgb"]))
    m_mono, _, _ = rawsr.load(str(out / weights["mono"]))
    rows, keep = [], {}
    for i in range(len(ds)):
        x, y, n, _ = ds[i]
        photo = ds.patch_files[i].stem.rsplit("_p", 1)[0]
        gt = y.permute(1, 2, 0).numpy()
        packed = to_lin(x.numpy())
        dem = to_gamma(demosaic(packed))  # 128 × 128
        outs = {
            "input": cv2.resize(dem, (256, 256), interpolation=cv2.INTER_CUBIC),
            "esrgan": base.run(dem, "esrgan"),
            "swin": base.run(dem, "swin"),
        }
        xt, nt = x[None].cuda(), n.view(1).cuda()
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            outs["riffle"] = m_rgb(xt, nt).float().clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
            outs["riffle_03"] = m_rgb(xt, nt + math.log2(0.3)).float().clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
            outs["riffle_mono"] = m_mono(xt, nt).float().clamp(0, 1)[0, 0].cpu().numpy()
        gt_m = mono(gt)
        row = {"patch": ds.patch_files[i].stem, "photo": photo}
        for k, o in outs.items():
            if k != "riffle_mono":
                row[f"{k}_psnr"], row[f"{k}_ssim"] = psnr(o, gt), ssim(o, gt)
                om = mono(o)
            else:
                om = o
            row[f"{k}_mono_psnr"], row[f"{k}_mono_ssim"] = psnr(om, gt_m), ssim(om, gt_m)
        rows.append(row)
        if photo not in NO_IMAGES:
            texture = float(cv2.Laplacian(cv2.GaussianBlur(gt_m, (0, 0), 1), cv2.CV_32F).var())
            if photo not in keep or texture > keep[photo][0]:
                keep[photo] = (texture, dem, outs, gt)
        if (i + 1) % 60 == 0:
            print(f"evaluated {i + 1} / {len(ds)} patches")
    return rows, keep


def synthetic_images(keep, out):
    """Grids of the most textured patch of a few held-out photos (no people)."""
    names = []
    for photo, (_, dem, outs, gt) in sorted(keep.items(), key=lambda kv: -kv[1][0])[:4]:
        inp = cv2.resize(dem, (256, 256), interpolation=cv2.INTER_NEAREST)
        cols = [inp, outs["esrgan"], outs["swin"], outs["riffle"], gt]
        labels = ["Input (demosaiced)", "Real-ESRGAN x2", "Swin2SR x2", "Riffle RGB", "Ground truth"]
        name = f"images/synthetic_rgb_{photo}.png"
        label_grid(cols, labels).save(out / name)
        cols_m = [mono(inp), mono(outs["esrgan"]), mono(outs["swin"]), mono(outs["riffle"]), outs["riffle_mono"], mono(gt)]
        labels_m = ["Input (demosaiced)", "Real-ESRGAN x2", "Swin2SR x2", "Riffle RGB to B&W", "Riffle B&W", "Ground truth"]
        name_m = f"images/synthetic_mono_{photo}.png"
        label_grid(cols_m, labels_m).save(out / name_m)
        names.append((photo, name, name_m))
    return names


def find_raw(stem):
    found = [p for p in (Path.home() / "Pictures" / "photos").rglob(f"{stem}.ORF") if "scans" not in p.parts]
    if not found:
        raise FileNotFoundError(f"{stem}.ORF")
    return found[0]


def real_crops(weights, out, base):
    """Crops of held-out RAWs, every method written as a DNG and rendered by darktable."""
    import rawpy

    m_rgb, _, _ = rawsr.load(str(out / weights["rgb"]))
    m_mono, _, _ = rawsr.load(str(out / weights["mono"]))
    names = []
    tmp = Path(tempfile.mkdtemp(prefix="riffle-release-"))
    try:
        for stem, cx, cy, what in REAL_CROPS:
            raw_path = find_raw(stem)
            packed, info = rawsr.read_packed(raw_path)
            tiff = rawsr._raw_tiff(raw_path)
            iso = (tiff.iso() if tiff else None) or 200
            flip, wb = info["flip"], info["wb"]
            _, H, W = packed.shape
            # The crop in the upright frame → packed px in the sensor's orientation
            up_w, up_h = (H, W) if flip in (5, 6) else (W, H)
            half = CROP // 4  # packed px
            ux, uy = int(cx * up_w), int(cy * up_h)
            box = {0: (ux, uy), 3: (W - ux, H - uy), 5: (W - uy, ux), 6: (uy, H - ux)}[flip]
            px, py = box
            px0, py0 = max(MARGIN, px - half), max(MARGIN, py - half)
            px0, py0 = min(px0, W - MARGIN - 2 * half), min(py0, H - MARGIN - 2 * half)
            ex0, ey0, ex1, ey1 = px0 - MARGIN, py0 - MARGIN, px0 + 2 * half + MARGIN, py0 + 2 * half + MARGIN
            wb4 = np.array([wb[0], wb[1], wb[1], wb[2]], np.float32)[:, None, None]
            x_in = rawsr.clip_highlights(packed[:, ey0:ey1, ex0:ex1], wb4)
            head = float(max(1.0, x_in.max()))
            disp = to_gamma(x_in / head).astype(np.float32)
            t = torch.from_numpy(disp)[None].cuda()
            with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
                level = torch.tensor([math.log2(iso / 200 * 0.3)], device="cuda")
                ours = m_rgb(t, level).float().clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
                ours_m = m_mono(t, level).float().clamp(0, 1)[0, 0].cpu().numpy()
            # The baselines start from LibRaw's AHD demosaic of the same area
            with rawpy.imread(str(raw_path)) as raw:
                ahd = raw.postprocess(demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD, output_color=rawpy.ColorSpace.raw,
                                      gamma=(1, 1), no_auto_bright=True, output_bps=16, use_camera_wb=False,
                                      user_wb=[1, 1, 1, 1], highlight_mode=rawpy.HighlightMode.Clip, user_flip=0)
            native = ahd[2 * ey0 : 2 * ey1, 2 * ex0 : 2 * ex1].astype(np.float32) / 65535 * wb[None, None, :]
            native = to_gamma(native / head).astype(np.float32)
            m4 = MARGIN * 4
            trim = lambda im: im[m4:-m4, m4:-m4]
            results = {
                "input": cv2.resize(native, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST),
                "esrgan": base.run(native, "esrgan"),
                "swin": base.run(native, "swin"),
                "riffle": ours,
            }
            # Each as a DNG (linear camera RGB, neutral grey for black and white), rendered alike
            rendered = {}
            variants = [(k, bw) for bw in (False, True) for k in results] + [("riffle_mono", True)]
            for k, bw in variants:
                lin = to_lin(trim(ours_m if k == "riffle_mono" else results[k])) * head
                if lin.ndim == 2:
                    lin = np.repeat(lin[..., None], 3, axis=2)
                elif bw:
                    lin = np.repeat((lin @ MIX)[..., None], 3, axis=2)
                dng = tmp / f"{stem}_{k}_{'bw' if bw else 'rgb'}.dng"
                rawsr.write_dng(dng, rawsr.upright(lin / wb[None, None, :].astype(np.float32), flip), info, "", "")
                tif = render(dng, dng.with_suffix(".tif"))
                with Image.open(tif) as im_t:
                    a = np.asarray(im_t)
                rendered[(k, bw)] = a.astype(np.float32) / (65535 if a.dtype == np.uint16 else 255)
            labels = ["Input (AHD demosaic)", "Real-ESRGAN x2", "Swin2SR x2", "Riffle RGB"]
            cols = [rendered[(k, False)] for k in ("input", "esrgan", "swin", "riffle")]
            name = f"images/real_rgb_{what}.png"
            label_grid(cols, labels).save(out / name)
            labels_m = ["Input (AHD demosaic)", "Real-ESRGAN x2", "Swin2SR x2", "Riffle RGB to B&W", "Riffle B&W"]
            cols_m = [rendered[(k, True)] for k in ("input", "esrgan", "swin", "riffle", "riffle_mono")]
            name_m = f"images/real_mono_{what}.png"
            label_grid(cols_m, labels_m).save(out / name_m)
            names.append((what, name, name_m))
            print(f"real crop: {what} ({stem}, ISO {iso})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return names


METHODS = [
    ("input", "Input: OpenCV demosaic + bicubic ×2"),
    ("esrgan", "Real-ESRGAN ×2 (x2plus) on the demosaic"),
    ("swin", "Swin2SR classical ×2 on the demosaic"),
    ("riffle_03", "Riffle RGB, denoise 0.3"),
    ("riffle", "Riffle RGB, denoise 1.0"),
]


def tables(rows):
    def mean(key):
        return float(np.mean([r[key] for r in rows]))

    rgb = ["| Method | PSNR (dB) | SSIM |", "|---|---:|---:|"]
    for k, name in METHODS:
        rgb.append(f"| {name} | {mean(f'{k}_psnr'):.2f} | {mean(f'{k}_ssim'):.4f} |")
    bw = ["| Method | PSNR (dB) | SSIM |", "|---|---:|---:|"]
    for k, name in [*METHODS, ("riffle_mono", "Riffle B&W (denoise 1.0)")]:
        name = name if k == "riffle_mono" else f"{name}, to B&W"
        bw.append(f"| {name} | {mean(f'{k}_mono_psnr'):.2f} | {mean(f'{k}_mono_ssim'):.4f} |")
    summary = {k: {m: mean(f"{k}_{m}") for m in ("psnr", "ssim", "mono_psnr", "mono_ssim") if f"{k}_{m}" in rows[0]}
               for k, _ in [*METHODS, ("riffle_mono", "")]}
    return "\n".join(rgb), "\n".join(bw), summary


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rgb", type=Path, default=HERE / "checkpoints_denoise" / "best_psnr.pt")
    ap.add_argument("--mono", type=Path, default=HERE / "checkpoints_mono_v2" / "best_psnr.pt")
    ap.add_argument("--out", type=Path, default=HERE / "huggingface")
    a = ap.parse_args()
    out = a.out
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "results").mkdir(exist_ok=True)

    weights = export_weights(a.rgb, a.mono, out)
    shutil.copy2(ROOT / "src" / "riffle" / "rawsr.py", out / "rawsr.py")

    base = Baselines()
    rows, keep = evaluate(weights, out, base)
    with open(out / "results" / "patches.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows({k: (f"{v:.5f}" if isinstance(v, float) else v) for k, v in row.items()} for row in rows)
    rgb_table, bw_table, summary = tables(rows)
    photos = sorted({r["photo"] for r in rows})
    (out / "results" / "summary.json").write_text(json.dumps(
        {"patches": len(rows), "photos": len(photos), "means": summary}, indent=2))
    synth = synthetic_images(keep, out)
    real = real_crops(weights, out, base)

    card = (HERE / "model_card.md").read_text()
    fill = {
        "RGB_TABLE": rgb_table,
        "BW_TABLE": bw_table,
        "N_PATCHES": str(len(rows)),
        "N_PHOTOS": str(len(photos)),
        "SYNTH_RGB": "\n\n".join(f"![{p}]({n})" for p, n, _ in synth),
        "SYNTH_BW": "\n\n".join(f"![{p}]({n})" for p, _, n in synth),
        "REAL_RGB": "\n\n".join(f"![{w}]({n})" for w, n, _ in real),
        "REAL_BW": "\n\n".join(f"![{w}]({n})" for w, _, n in real),
    }
    for key, value in fill.items():
        card = card.replace("{{" + key + "}}", value)
    (out / "README.md").write_text(card)
    print(rgb_table, bw_table, sep="\n\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
