"""Cuts RAWs into quad-centred Bayer plane patches for dataset.BayerPatchDataset."""
import argparse
import csv
import json
import multiprocessing
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from dataset import GAMMA, read_planes


def select_patches(planes, wb, size, max_patches, flat_frac, rng):
    """
    Origins (on the 4 px superblock grid) of the most textured patches, plus a share of
    random ones, so flat areas and their noise are learnt too.
    """
    g = 0.5 * (planes[1] + planes[2]) * wb[1]
    g = np.power(np.clip(g / max(1.0, g.max()), 0, 1), 1 / GAMMA)
    # Smoothed first, so noise does not count as texture
    lap = cv2.Laplacian(cv2.GaussianBlur(g, (0, 0), 1), cv2.CV_32F)

    H, W = g.shape
    step = size // 2
    cands = [(float(lap[y:y + size, x:x + size].var()), y, x)
             for y in range(0, H - size + 1, step) for x in range(0, W - size + 1, step)]
    cands.sort(reverse=True)
    n_flat = int(round(max_patches * flat_frac))
    chosen = cands[:max_patches - n_flat]
    rest = cands[max_patches - n_flat:]
    for i in rng.permutation(len(rest))[:n_flat]:
        chosen.append(rest[i])
    return [(y, x) for _, y, x in chosen]


def process_single_raw(task):
    raw_path, out_dir, size, max_patches, flat_frac = task
    try:
        planes, wb, iso, noise = read_planes(raw_path)
        rng = np.random.default_rng(zlib.crc32(raw_path.name.encode()))
        count = 0
        for i, (y, x) in enumerate(select_patches(planes, wb, size, max_patches, flat_frac, rng)):
            patch = (planes[:, y:y + size, x:x + size] * 65535.0 + 0.5).astype(np.uint16)
            np.savez_compressed(out_dir / f"{raw_path.stem}_p{i:02d}.npz",
                                planes=patch, wb=wb, iso=np.float32(iso or 0))
            count += 1
        return raw_path.name, count, iso, noise
    except Exception as e:
        print(f"Error processing {raw_path.name}: {e}")
        return raw_path.name, 0, None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("experiments/raw_sr/raws"), help="Folder with .ORF files (searched recursively)")
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/raw_sr/data_binned"), help="Output folder (train/, val/, noise.json, noise.csv)")
    parser.add_argument("--patch-size", type=int, default=320, help="Stored patch size in target px (= 2× in RAW px); training crops from it")
    parser.add_argument("--max-patches", type=int, default=24, help="Patches per RAW")
    parser.add_argument("--flat-frac", type=float, default=0.2, help="Share of patches picked at random instead of by texture")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    if args.patch_size % 4:
        raise ValueError("--patch-size must be a multiple of 4")

    raws = sorted(p for p in args.data_dir.rglob("*") if p.suffix.lower() == ".orf" and "scans" not in p.parts)
    if not raws:
        raise ValueError(f"No .ORF files found in {args.data_dir}")

    # Held-out photos, spread evenly over the collection
    val_count = min(15, max(2, len(raws) // 20))
    val_idx = set(np.linspace(0, len(raws) - 1, val_count).round().astype(int).tolist())

    train_dir, val_dir = args.out_dir / "train", args.out_dir / "val"
    train_dir.mkdir(parents=True, exist_ok=True)
    val_dir.mkdir(parents=True, exist_ok=True)

    tasks = [(r, val_dir if i in val_idx else train_dir, args.patch_size, args.max_patches, args.flat_frac)
             for i, r in enumerate(raws)]
    print(f"{len(raws) - len(val_idx)} train and {len(val_idx)} val RAWs, {args.workers} workers")

    total, per_iso200 = 0, []
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as ex, \
            open(args.out_dir / "noise.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["file", "patches", "iso", "a", "b", "a_at_iso200"])
        for name, count, iso, noise in tqdm(ex.map(process_single_raw, tasks), total=len(tasks), desc="RAWs"):
            total += count
            if noise is not None and iso:
                a, b = noise
                per_iso200.append((a * 200 / iso, b * 200 / iso))
                writer.writerow([name, count, iso, f"{a:.3e}", f"{b:.3e}", f"{a * 200 / iso:.3e}"])

    # Per photo the estimate is pulled up by texture; the median over the library is not
    if not per_iso200:
        raise ValueError("No noise estimates (no ISO in the EXIF?)")
    a, b = np.median(np.array(per_iso200), axis=0)
    with open(args.out_dir / "noise.json", "w") as f:
        json.dump({"a": float(a), "b": float(b)}, f)
    print(f"{total} patches in {args.out_dir}")
    print(f"Pixel noise at ISO 200: variance {a:.3e}·s + {b:.3e}, σ {np.sqrt(a * 0.18 + b):.4f} at s = 0.18")


if __name__ == "__main__":
    main()
