"""Compares the lightweight model with the Swin2SR one: quality and speed, separately from
the release.

    venv/bin/python experiments/raw_sr/eval_light.py --light checkpoints_light/best_psnr.pt
        [--light-mono checkpoints_light_mono/best_psnr.pt] [--out light_eval]

The same protocol as make_release.py: every held-out patch (noise gain 1, blur sigma
1.25-3.1, denoise 1.0), PSNR and SSIM in colour and black and white; seconds per 20 MP frame
for the network; grids of a few patches and crops of held-out RAWs (Riffle default denoise
0.3), developed by darktable from DNGs. Writes report.md, metrics.json and images/.
"""

import argparse
import json
import math
import shutil
import tempfile
from pathlib import Path

import numpy as np
import torch
from PIL import Image

import make_release as R
from dataset import BayerPatchDataset
from model_light import load_light, seconds_per_frame
from riffle import rawsr

HERE = Path(__file__).parent


def run(model, x, n):
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
        return model(x, n).float().clamp(0, 1)[0].cpu().numpy()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--light", type=Path, required=True, help="light colour checkpoint")
    ap.add_argument("--light-mono", type=Path, help="light black-and-white checkpoint")
    ap.add_argument("--big", default=str(HERE / "huggingface" / "riffle-raw-sr-rgb.safetensors"))
    ap.add_argument("--big-mono", default=str(HERE / "huggingface" / "riffle-raw-sr-mono.safetensors"))
    ap.add_argument("--out", type=Path, default=HERE / "light_eval")
    a = ap.parse_args()
    (a.out / "images").mkdir(parents=True, exist_ok=True)

    models = {"swin": rawsr.load(a.big)[0], "light": load_light(a.light, "cuda")}
    if a.light_mono:
        models["swin_mono"] = rawsr.load(a.big_mono)[0]
        models["light_mono"] = load_light(a.light_mono, "cuda")
    speed = {k: seconds_per_frame(m) for k, m in models.items()}

    ds = BayerPatchDataset(HERE / "data_binned" / "val", patch_size=256, is_train=False, noise_gain=(1.0, 1.0),
                           blur=(1.25, 3.1), target_blur=0.5, partial_denoise=0.0)
    scores = {k: {"psnr": [], "ssim": [], "mono_psnr": [], "mono_ssim": []} for k in models}
    keep = {}
    for i in range(len(ds)):
        x, y, n, _ = ds[i]
        photo = ds.patch_files[i].stem.rsplit("_p", 1)[0]
        gt = y.permute(1, 2, 0).numpy()
        gt_m = R.mono(gt)
        xt, nt = x[None].cuda(), n.view(1).cuda()
        outs = {}
        for k, m in models.items():
            o = run(m, xt, nt)
            if o.shape[0] == 3:
                o = o.transpose(1, 2, 0)
                scores[k]["psnr"].append(R.psnr(o, gt))
                scores[k]["ssim"].append(R.ssim(o, gt))
                om = R.mono(o)
            else:
                om = o[0]
            scores[k]["mono_psnr"].append(R.psnr(om, gt_m))
            scores[k]["mono_ssim"].append(R.ssim(om, gt_m))
            outs[k] = o if o.ndim == 3 else om
        if photo not in R.NO_IMAGES:
            texture = float(np.var(np.diff(gt_m, axis=0)))
            if photo not in keep or texture > keep[photo][0]:
                keep[photo] = (texture, outs, gt)
    means = {k: {s: float(np.mean(v)) for s, v in d.items() if v} for k, d in scores.items()}

    names = {"swin": "Swin2SR (current)", "light": "Light", "swin_mono": "Swin2SR B&W (current)", "light_mono": "Light B&W"}
    lines = ["| Model | PSNR (dB) | SSIM | B&W PSNR (dB) | B&W SSIM | s per 20 MP frame |", "|---|---:|---:|---:|---:|---:|"]
    for k in models:
        m = means[k]
        lines.append(f"| {names[k]} | {m.get('psnr', float('nan')):.2f} | {m.get('ssim', float('nan')):.4f} | "
                     f"{m['mono_psnr']:.2f} | {m['mono_ssim']:.4f} | {speed[k]:.1f} |")
    table = "\n".join(lines)

    images = []
    for photo, (_, outs, gt) in sorted(keep.items(), key=lambda kv: -kv[1][0])[:4]:
        name = f"images/patch_{photo}.png"
        R.label_grid([outs["swin"], outs["light"], gt], ["Swin2SR (current)", "Light", "Ground truth"]).save(a.out / name)
        images.append(name)

    # Real crops: the same held-out RAWs as the release, both models, developed by darktable
    tmp = Path(tempfile.mkdtemp(prefix="riffle-light-"))
    try:
        for stem, cx, cy, what in R.REAL_CROPS:
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
            level = torch.tensor([math.log2(iso / 200 * 0.3)], device="cuda")
            m4 = R.MARGIN * 4
            cols = []
            for k in ("swin", "light"):
                o = run(models[k], t, level).transpose(1, 2, 0)[m4:-m4, m4:-m4]
                dng = tmp / f"{stem}_{k}.dng"
                rawsr.write_dng(dng, rawsr.upright(R.to_lin(o) * head / wb[None, None, :].astype(np.float32), flip), info, "", "")
                with Image.open(R.render(dng, dng.with_suffix(".tif"))) as im:
                    arr = np.asarray(im)
                cols.append(arr.astype(np.float32) / (65535 if arr.dtype == np.uint16 else 255))
            name = f"images/real_{what}.png"
            R.label_grid(cols, ["Swin2SR (current)", "Light"], title=f"{what}, ISO {iso}, denoise 0.3").save(a.out / name)
            images.append(name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    (a.out / "metrics.json").write_text(json.dumps({"means": means, "seconds_per_frame": speed,
                                                    "patches": len(ds), "light": str(a.light)}, indent=2))
    report = ["# Light model vs Swin2SR", "", f"{len(ds)} held-out patches; light checkpoint `{a.light}`.", "", table, "",
              *[f"![]({n})\n" for n in images]]
    (a.out / "report.md").write_text("\n".join(report))
    print(table)
    print(f"\nwritten to {a.out}")


if __name__ == "__main__":
    main()
