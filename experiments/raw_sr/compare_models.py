"""Compares Swin2SR checkpoints side by side: numbers and real crops.

    venv/bin/python experiments/raw_sr/compare_models.py \\
        --rgb base=checkpoints/best_psnr.pt --rgb noise=checkpoints_exp_noise/latest.pt ... \\
        [--mono base=checkpoints_mono/best_psnr.pt --mono ...] [--out compare_exp]

- Every held-out patch, as make_release.py (noise gain 1, blur sigma 1.25-3.1): PSNR and SSIM
  at denoise 1.0 (colour models also as black and white).
- Noise left at denoise 0.1, on the same patches: the fine (high-pass) part of the output's
  colour differences (R − G, B − G) and of its brightness. Colour noise is what looks digital.
- Crops of the held-out RAWs (the release's) at denoise 0.3, and the ISO 4000 duck also at
  0.1, every model's result written as a DNG and developed by darktable.
Writes report.md and images/ into --out.
"""

import argparse
import math
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

import make_release as R
from dataset import BayerPatchDataset
from imgmind import rawsr

HERE = Path(__file__).parent


def named(values):
    out = {}
    for v in values or []:
        name, _, path = v.partition("=")
        p = Path(path)
        out[name] = str(p if p.is_absolute() or p.exists() else HERE / p)
    return out


def run(model, x, level):
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
        o = model(x, level).float().clamp(0, 1)[0].cpu().numpy()
    return o.transpose(1, 2, 0) if o.shape[0] == 3 else o[0]


def highpass(im):
    return im - cv2.GaussianBlur(im, (0, 0), 1.5)


def crop_input(stem, cx, cy):
    """A real crop as the model's input (packed, gamma) and what writing it back needs."""
    raw_path = R.find_raw(stem)
    packed, info = rawsr.read_packed(raw_path)
    tiff = rawsr._raw_tiff(raw_path)
    iso = (tiff.iso() if tiff else None) or 200
    flip, wb = info["flip"], info["wb"]
    _, H, W = packed.shape
    up_w, up_h = (H, W) if flip in (5, 6) else (W, H)
    half = R.CROP // 4
    ux, uy = int(cx * up_w), int(cy * up_h)
    px, py = {0: (ux, uy), 3: (W - ux, H - uy), 5: (W - uy, ux), 6: (uy, H - ux)}[flip]
    px0 = min(max(R.MARGIN, px - half), W - R.MARGIN - 2 * half)
    py0 = min(max(R.MARGIN, py - half), H - R.MARGIN - 2 * half)
    box = (slice(py0 - R.MARGIN, py0 + 2 * half + R.MARGIN), slice(px0 - R.MARGIN, px0 + 2 * half + R.MARGIN))
    wb4 = np.array([wb[0], wb[1], wb[1], wb[2]], np.float32)[:, None, None]
    x_in = rawsr.clip_highlights(packed[:, box[0], box[1]], wb4)
    head = float(max(1.0, x_in.max()))
    t = torch.from_numpy(R.to_gamma(x_in / head).astype(np.float32))[None].cuda()
    return t, iso, info, head


def develop(out, info, head, tmp, name):
    """An output (gamma, cropped with margin) → DNG → darktable → array."""
    m4 = R.MARGIN * 4
    o = out[m4:-m4, m4:-m4]
    lin = R.to_lin(o) * head
    if lin.ndim == 2:
        lin = np.repeat(lin[..., None], 3, axis=2)
    wb = info["wb"]
    dng = tmp / f"{name}.dng"
    rawsr.write_dng(dng, rawsr.upright(lin / wb[None, None, :].astype(np.float32), info["flip"]), info, "", "")
    with Image.open(R.render(dng, dng.with_suffix(".tif"))) as im:
        a = np.asarray(im)
    return a.astype(np.float32) / (65535 if a.dtype == np.uint16 else 255)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rgb", action="append", help="name=checkpoint (colour)")
    ap.add_argument("--mono", action="append", help="name=checkpoint (black and white)")
    ap.add_argument("--out", type=Path, default=HERE / "compare_exp")
    a = ap.parse_args()
    groups = {"rgb": named(a.rgb), "mono": named(a.mono)}
    (a.out / "images").mkdir(parents=True, exist_ok=True)
    models = {(g, n): rawsr.load(p)[0] for g, d in groups.items() for n, p in d.items()}

    ds = BayerPatchDataset(HERE / "data_binned" / "val", patch_size=256, is_train=False, noise_gain=(1.0, 1.0),
                           blur=(1.25, 3.1), target_blur=0.5, partial_denoise=0.0)
    acc = {k: {"psnr": [], "ssim": [], "bw_psnr": [], "bw_ssim": [], "chroma_noise": [], "luma_noise": []} for k in models}
    for i in range(len(ds)):
        x, y, n, _ = ds[i]
        gt = y.permute(1, 2, 0).numpy()
        gt_m = R.mono(gt)
        xt, nt = x[None].cuda(), n.view(1).cuda()
        for (g, name), m in models.items():
            o = run(m, xt, nt)
            s = acc[(g, name)]
            if o.ndim == 3:
                s["psnr"].append(R.psnr(o, gt)); s["ssim"].append(R.ssim(o, gt))
                om = R.mono(o)
            else:
                om = o
            s["bw_psnr"].append(R.psnr(om, gt_m)); s["bw_ssim"].append(R.ssim(om, gt_m))
            o01 = run(m, xt, nt + math.log2(0.1))  # denoise 0.1: the noise each leaves
            if o01.ndim == 3:
                s["chroma_noise"].append(float(np.std(highpass(o01[..., 0] - o01[..., 1])) + np.std(highpass(o01[..., 2] - o01[..., 1]))) / 2)
                s["luma_noise"].append(float(np.std(highpass(R.mono(o01)))))
            else:
                s["luma_noise"].append(float(np.std(highpass(o01))))
        if (i + 1) % 90 == 0:
            print(f"{i + 1} / {len(ds)} patches")
    mean = {k: {m: float(np.mean(v)) if v else None for m, v in d.items()} for k, d in acc.items()}

    fmt = lambda v, f: "–" if v is None else format(v, f)
    lines = []
    for g in ("rgb", "mono"):
        if not groups[g]:
            continue
        lines += [f"### {'Colour' if g == 'rgb' else 'Black and white'}", ""]
        if g == "rgb":
            lines += ["| Model | PSNR | SSIM | B&W PSNR | B&W SSIM | colour noise @0.1 | brightness noise @0.1 |", "|---|---:|---:|---:|---:|---:|---:|"]
        else:
            lines += ["| Model | PSNR | SSIM | brightness noise @0.1 |", "|---|---:|---:|---:|"]
        for name in groups[g]:
            m = mean[(g, name)]
            if g == "rgb":
                lines.append(f"| {name} | {fmt(m['psnr'], '.2f')} | {fmt(m['ssim'], '.4f')} | {fmt(m['bw_psnr'], '.2f')} | "
                             f"{fmt(m['bw_ssim'], '.4f')} | {fmt(m['chroma_noise'] * 1000, '.2f')} | {fmt(m['luma_noise'] * 1000, '.2f')} |")
            else:
                lines.append(f"| {name} | {fmt(m['bw_psnr'], '.2f')} | {fmt(m['bw_ssim'], '.4f')} | {fmt(m['luma_noise'] * 1000, '.2f')} |")
        lines.append("")
    table = "\n".join(lines)

    images = []
    tmp = Path(tempfile.mkdtemp(prefix="imgmind-compare-"))
    try:
        crops = [(s, cx, cy, w, 0.3) for s, cx, cy, w in R.REAL_CROPS] + [(s, cx, cy, w, 0.1) for s, cx, cy, w in R.REAL_CROPS if w == "duck"]
        for stem, cx, cy, what, dn in crops:
            t, iso, info, head = crop_input(stem, cx, cy)
            level = torch.tensor([math.log2(iso / 200 * dn)], device="cuda")
            for g in ("rgb", "mono"):
                if not groups[g]:
                    continue
                cols = [develop(run(models[(g, n)], t, level), info, head, tmp, f"{stem}_{g}_{n}_{dn}") for n in groups[g]]
                name = f"images/{g}_{what}_denoise{dn}.png"
                R.label_grid(cols, list(groups[g]), title=f"{what}, ISO {iso}, denoise {dn}").save(a.out / name)
                images.append(name)
            print(f"crop: {what} at denoise {dn}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    paths = "\n".join(f"- {g} {n}: `{p}`" for g, d in groups.items() for n, p in d.items())
    (a.out / "report.md").write_text("\n".join([
        "# Model comparison", "", paths, "",
        f"{len(ds)} held-out patches. PSNR/SSIM at denoise 1.0; noise columns: the fine part (high-pass) of the "
        "output's colour differences and brightness at denoise 0.1, ×1000 (lower: less noise kept).", "",
        table, *[f"![]({n})\n" for n in images]]))
    print(table)
    print(f"written to {a.out}")


if __name__ == "__main__":
    main()
