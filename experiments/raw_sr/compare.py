"""Render a RAW and DNGs made from it with darktable-cli, and compare them.

    venv/bin/python experiments/raw_sr/compare.py IN.ORF A.dng B.dng … --out DIR [--crop x,y,size]

darktable runs with a throwaway config and an in-memory library (your darktable setup is
not touched) and its default processing, so the RAW and each DNG are developed the same
way. Printed per rendering: its mean colour (CIELAB L, a, b) and the difference of those
means from the RAW's (ΔE); then, with the RAW's rendering matched onto the DNG's (SIFT, a
similarity: a DNG may be cropped, straightened, upscaled or framed differently by lens
correction), the pixel-by-pixel ΔE (on both slightly blurred, so detail and demosaicing
differences count less than colour) and how well the two line up (median feature error).
The crops show the same place in each rendering. Written to DIR: the renderings,
and one image of 100 % crops side by side (the RAW's crop enlarged to match, so detail can
be compared at the DNG's scale).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from raw_to_dng import match  # noqa: E402


def render(src: Path, out: Path) -> Path:
    out.unlink(missing_ok=True)  # (darktable-cli writes another name beside an existing file)
    config = Path(tempfile.mkdtemp(prefix="dt-config-"))
    try:
        subprocess.run(
            ["darktable-cli", str(src), str(out), "--hq", "true", "--core", "--configdir", str(config),
             "--library", ":memory:", "--conf", "plugins/imageio/format/tiff/bpp=16"],
            check=True, capture_output=True, timeout=900,
        )
    finally:
        shutil.rmtree(config, ignore_errors=True)
    return out


def lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (0-1) to CIELAB (D65)."""
    c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def load(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        a = np.asarray(im)
    return a.astype(np.float32) / (65535.0 if a.dtype == np.uint16 else 255.0)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("raw", type=Path)
    ap.add_argument("dngs", type=Path, nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--crop", default="", help="x,y,size: the crops' centre (fractions of the first DNG's frame) and size (RAW pixels)")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)

    ref_path = render(a.raw, a.out / f"{a.raw.stem}.tif")
    ref = load(ref_path)
    H, W = ref.shape[:2]
    mean_lab = lambda x: lab(x.reshape(-1, 3)[:: 97]).mean(axis=0)
    ref_lab = mean_lab(ref)
    print(f"{a.raw.name}: {W}x{H}, mean Lab {np.round(ref_lab, 1)}")

    import cv2

    fx, fy, size = (0.5, 0.5, 400)
    if a.crop:
        fx, fy, size = (float(v) for v in a.crop.split(","))
        size = int(size)
    ref8 = (ref * 255).astype(np.uint8)
    panels = []
    for dng in a.dngs:
        out = render(dng, a.out / f"{dng.stem}.tif")
        img = load(out)
        h, w = img.shape[:2]
        d_lab = mean_lab(img)
        line = f"{dng.name}: {w}x{h}, mean Lab {np.round(d_lab, 1)}, ΔE of the means {np.sqrt(((d_lab - ref_lab) ** 2).sum()):.2f}"
        # The RAW's rendering, matched and warped onto this one.
        M, info = match(ref8, (img * 255).astype(np.uint8))
        aligned = cv2.warpAffine(ref, M[:2], (w, h), flags=cv2.INTER_LINEAR, borderValue=(-1, -1, -1))
        valid = (aligned >= 0).all(axis=2)
        sigma = max(1.0, 1.5 * info["scale"])
        blur = lambda x: cv2.GaussianBlur(x, (0, 0), sigma)
        de = np.sqrt(((lab(blur(np.clip(img, 0, 1))) - lab(blur(np.clip(aligned, 0, 1)))) ** 2).sum(-1))[valid]
        print(f"{line}; aligned x{info['scale']:.3f} ({info['inliers']} features, {info['error_px']} px apart): "
              f"ΔE median {np.median(de):.2f}, 95th pct {np.percentile(de, 95):.2f}")
        cx, cy = int(fx * w), int(fy * h)
        side = int(size * info["scale"])
        box = (max(0, cx - side // 2), max(0, cy - side // 2))
        crop = lambda x: Image.fromarray((np.clip(x[box[1] : box[1] + side, box[0] : box[0] + side], 0, 1) * 255).astype(np.uint8))
        if not panels:
            panels.append((f"{a.raw.stem} (RAW)", crop(aligned)))
        panels.append((dng.stem, crop(img)))
    side = max(p[1].width for p in panels)
    sheet = Image.new("RGB", (side * len(panels) + 10 * (len(panels) - 1), side + 24), "white")
    for i, (name, im) in enumerate(panels):
        sheet.paste(im.resize((side, side), Image.Resampling.NEAREST), (i * (side + 10), 24))
        ImageDraw.Draw(sheet).text((i * (side + 10) + 4, 4), name, fill="black")
    sheet.save(a.out / "crops.png")
    print(f"crops: {a.out / 'crops.png'}")


if __name__ == "__main__":
    main()
