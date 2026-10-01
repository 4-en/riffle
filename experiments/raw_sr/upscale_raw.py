"""Bayer RAW → ×2 linear DNG with the trained Bayer models (RGB or monochrome).

    venv/bin/python experiments/raw_sr/upscale_raw.py IN.ORF [-o OUT.dng] [--model rgb | mono]
        [--checkpoint PT] [--crop x,y,w,h] [--denoise F] [--filter red | --mix r,g,b | --plain-mix]

The model replaces demosaicing and upscaling: it takes the packed Bayer data (white-balanced,
scaled to its peak, gamma 2.2, as in training) and returns RGB, or monochrome, at twice the
RAW's resolution. The result is turned upright afterwards (turning the mosaic first would move
its colours to other places in the 2×2 quad). The DNG opens in darktable like the RAW: linear
camera RGB, the camera's colour matrix and white balance, its EXIF. A monochrome result is
written as neutral grey in that space. No lens corrections or Riffle crop (raw_to_dng.py has
those for the other models).
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import rawpy

sys.path.insert(0, str(Path(__file__).parent))
from dataset import FILTERS, GAMMA, MONO_MIX, luminance_weights, mix_gains, read_iso
from raw_to_dng import EXIF_TAGS, RawTiff, append_exif, tiled, upright, write_dng

HERE = Path(__file__).parent
MARGIN = 16  # packed px of context around a crop, trimmed afterwards


def read_packed(path):
    """(4, H/2, W/2) R G1 G2 B, black 0, white 1, in the sensor's orientation; and the info
    write_dng needs (as-shot white balance, colour matrix, LibRaw's flip code)."""
    with rawpy.imread(str(path)) as raw:
        desc = raw.color_desc.decode()
        pattern = raw.raw_pattern
        if "".join(desc[c] for c in pattern.flat) != "RGGB":
            raise SystemExit("only RGGB sensors are supported")
        img = raw.raw_image_visible.astype(np.float32)
        black = np.array(raw.black_level_per_channel, np.float32)
        white = float(raw.white_level)
        wb = np.array(raw.camera_whitebalance[:3], np.float64)
        if not wb.any():
            wb = np.array(raw.daylight_whitebalance[:3], np.float64)
        info = {"wb": wb / wb[1], "cam_xyz": np.array(raw.rgb_xyz_matrix[:3], np.float64), "flip": raw.sizes.flip}
    H, W = img.shape[0] // 2 * 2, img.shape[1] // 2 * 2
    packed = [np.clip((img[dy:H:2, dx:W:2] - black[c]) / (white - black[c]), 0, 1)
              for (dy, dx), c in zip([(0, 0), (0, 1), (1, 0), (1, 1)], pattern.flat)]
    return np.stack(packed), info


def clip_highlights(packed, scale):
    """
    `packed` (4, h, w, black 0 and white 1) times `scale` (white balance and gains), with
    clipped highlights made neutral. Where one channel reaches the white level, the others
    still rise, so after white balance a clipped green turns magenta (and the model reproduces
    it). In quads with a clipped channel (and their neighbours) every channel is capped at the
    lowest channel's clip level, as darktable's "clip highlights" does.
    """
    import cv2

    out = packed * scale
    clipped = (packed >= 0.999).any(axis=0).astype(np.uint8)
    clipped = cv2.dilate(clipped, np.ones((3, 3), np.uint8)).astype(bool)
    level = float(scale.min())
    out[:, clipped] = np.minimum(out[:, clipped], level)
    return out


def sensor_box(crop, flip):
    """A crop (x, y, w, h, fractions of the upright frame) in the sensor's orientation."""
    x, y, w, h = crop
    if flip == 3:  # half a turn
        return 1 - x - w, 1 - y - h, w, h
    if flip == 6:  # upright = sensor turned clockwise
        return y, 1 - x - w, h, w
    if flip == 5:  # anticlockwise
        return 1 - y - h, x, h, w
    return x, y, w, h


def load_model(kind, checkpoint):
    if kind == "mono":
        from model_mono import BayerSwin2SRMono as Net
    else:
        from model import BayerSwin2SR as Net
    net = Net()
    net.load_checkpoint(checkpoint)
    return net.cuda().eval()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("raw", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="default: IN_x2.dng (IN_x2_mono.dng) beside the RAW")
    ap.add_argument("--model", choices=["rgb", "mono"], default="rgb")
    ap.add_argument("--checkpoint", type=Path, help="default: checkpoints/best_psnr.pt (checkpoints_mono/ for mono)")
    ap.add_argument("--crop", help="x,y,w,h: fractions of the upright frame (a whole frame takes minutes)")
    ap.add_argument("--denoise", type=float, default=1.0,
                    help="noise level the model is told, relative to the photo's (from its ISO): below 1 keeps more fine texture and grain, above 1 smooths more")
    ap.add_argument("--fp32", action="store_true", help="full precision (default: fp16 autocast, as in training)")
    mono = ap.add_argument_group("monochrome mix (default: the photo's luminance, non-negative fit)")
    mono.add_argument("--filter", choices=list(FILTERS), default="none", help="a black-and-white filter on top of the luminance")
    mono.add_argument("--mix", help="r,g,b: own weights on white-balanced camera RGB (non-negative)")
    mono.add_argument("--plain-mix", action="store_true", help="the model's own mix, (R + 2G + B) / 4, no input gains")
    a = ap.parse_args(argv)

    if a.model == "rgb" and (a.mix or a.plain_mix or a.filter != "none"):
        ap.error("--filter, --mix and --plain-mix are for --model mono")
    checkpoint = a.checkpoint or HERE / ("checkpoints_mono" if a.model == "mono" else "checkpoints") / "best_psnr.pt"
    out = a.out or a.raw.with_name(a.raw.stem + ("_x2_mono.dng" if a.model == "mono" else "_x2.dng"))

    t0 = time.perf_counter()
    packed, info = read_packed(a.raw)
    wb = info["wb"]
    wb4 = np.array([wb[0], wb[1], wb[1], wb[2]], np.float32)[:, None, None]

    gains = np.ones(3, np.float32)
    if a.model == "mono" and not a.plain_mix:
        if a.mix:
            gains = mix_gains(mix=[float(v) for v in a.mix.split(",")])
        else:
            lum = luminance_weights(a.raw, packed * wb4)
            gains = mix_gains(filter=a.filter, luminance=lum)
        print(f"mix {np.round(MONO_MIX * gains, 3)} (R, G, B), input gains {np.round(gains, 3)}")

    # The crop on the packed grid (whole quads), with context around it
    _, H, W = packed.shape
    if a.crop:
        x, y, w, h = sensor_box([float(v) for v in a.crop.split(",")], info["flip"])
        px0, py0 = int(x * W), int(y * H)
        px1, py1 = min(W, int(round((x + w) * W))), min(H, int(round((y + h) * H)))
    else:
        px0, py0, px1, py1 = 0, 0, W, H
    ex0, ey0, ex1, ey1 = max(0, px0 - MARGIN), max(0, py0 - MARGIN), min(W, px1 + MARGIN), min(H, py1 + MARGIN)
    x_in = clip_highlights(packed[:, ey0:ey1, ex0:ex1], wb4 * gains[[0, 1, 1, 2]][:, None, None])
    head = float(max(1.0, x_in.max()))
    disp = np.power(np.clip(x_in / head, 0, 1), 1 / GAMMA).astype(np.float32)
    t_read = time.perf_counter() - t0

    import torch

    net = load_model(a.model, checkpoint)
    iso = read_iso(a.raw) or 200
    noise = torch.tensor([np.log2(iso / 200 * a.denoise)], dtype=torch.float32, device="cuda")
    if not net.noise_conditioned:
        noise = None
        if a.denoise != 1.0:
            print("warning: this checkpoint has no noise input; --denoise has no effect")

    def run(t):
        with torch.autocast("cuda", dtype=torch.float16, enabled=not a.fp32):
            r = net(t, noise).float().clamp(0, 1)
        return r.expand(-1, 3, -1, -1) if r.shape[1] == 1 else r

    t1 = time.perf_counter()
    big = tiled(np.moveaxis(disp, 0, -1), 4, run, multiple=8)  # packed → ×4 = RAW ×2
    t_up = time.perf_counter() - t1
    big = big[(py0 - ey0) * 4:(py1 - ey0) * 4, (px0 - ex0) * 4:(px1 - ex0) * 4]

    lin = np.power(np.clip(big, 0, 1), GAMMA) * head
    if a.model == "mono":
        lin = lin[..., :1] / wb[None, None, :]  # grey: equal after the white balance
    else:
        lin = lin / wb[None, None, :]
    lin = upright(lin, info["flip"])

    tiff = RawTiff(a.raw)
    write_dng(out, lin, info, tiff.text(271), tiff.text(272))
    append_exif(out, {c: v for c, v in tiff.exif.items() if c in EXIF_TAGS}, tiff.order)
    print(f"{out}: {lin.shape[1]}×{lin.shape[0]}, read {t_read:.1f} s, model {t_up:.1f} s, "
          f"{out.stat().st_size / 1e6:.0f} MB, ISO {iso}, denoise {a.denoise}, {checkpoint}")


if __name__ == "__main__":
    main()
