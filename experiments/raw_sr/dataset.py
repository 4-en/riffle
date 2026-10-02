"""Synthetic Bayer → ×2 RGB training pairs from the camera's own RAWs, with no demosaicing.

- planes: the RAW's four Bayer planes (R, G1, G2, B), each shifted a quarter of its pitch
  to the centre of its 2×2 quad (Lanczos), so all four are sampled at the same points.
- target: per quad (R, (G1 + G2) / 2, B), linear RGB at half the RAW's resolution.
- input: a Bayer mosaic at a quarter of it. Each 2×2 block of quads is averaged per colour
  (what a sensor with pixels twice as large records), one colour is kept per site in RGGB
  order, and noise is added so each site is about as noisy as one real pixel at that level.

The input and target are averages of real sensor pixels; the re-mosaic only drops colours,
as a sensor does. Packed input (4, P/4, P/4) → target (3, P, P) is ×4 from packed, ×2 over
the mosaic: the same ratio as a native RAW's packed Bayer → ×2 RGB at inference.
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np
import rawpy
import torch
from torch.utils.data import Dataset

GAMMA = 2.2

# Shift (dy, dx) in packed px that moves each plane's samples to its quad's centre:
# R sits at native (0, 0) of the quad, G1 at (0, 1), G2 at (1, 0), B at (1, 1).
PLANE_SHIFTS = [(0.25, 0.25), (0.25, -0.25), (-0.25, 0.25), (-0.25, -0.25)]


def _lanczos4_weights(t):
    """OpenCV's INTER_LANCZOS4 weights for a fractional offset t."""
    x = np.arange(-3, 5) - t
    w = np.sinc(x) * np.sinc(x / 4)
    return w / w.sum()


_w = _lanczos4_weights(0.25)
SHIFT_NOISE = float((_w ** 2).sum()) ** 2  # a shifted plane's share of one pixel's variance


def residual_noise(sigma):
    """
    The patch's own noise left in an input site, as a fraction of one pixel's variance: after
    the Lanczos shift, the input blur (sigma, as cv2.GaussianBlur makes it) and the 2 × 2
    average of quads. Per axis the three are one filter h; for white noise the share is
    (Σh²)², counting the correlation the shift and blur leave between neighbours (0.25 without
    blur, 0.02 at sigma 2; matches a simulation to 1 %). The rest is added in the dataset.
    """
    h = _w
    if sigma >= 0.05:
        k = cv2.getGaussianKernel(int(round(sigma * 8 + 1)) | 1, sigma, cv2.CV_64F).ravel()
        h = np.convolve(h, k)
    h = np.convolve(h, [0.5, 0.5])
    return float((h ** 2).sum()) ** 2


def read_iso(path):
    """ISO speed from the RAW's EXIF, or None."""
    import struct
    from raw_to_dng import RawTiff
    tiff = RawTiff(Path(path))
    entry = tiff.exif.get(0x8827)
    return struct.unpack(tiff.order + "H", entry[2][:2])[0] if entry else None


def read_planes(path):
    """
    Returns (planes, wb, iso, noise):
      planes: (4, H/2, W/2) float32, R G1 G2 B, black 0, white 1, before white balance,
              each shifted to its quad's centre
      wb:     (3,) camera white balance, G = 1
      iso:    from the EXIF (None if missing)
      noise:  (a, b) of this photo's pixel variance a·s + b at level s (see estimate_noise)
    """
    with rawpy.imread(str(path)) as raw:
        desc = raw.color_desc.decode()
        pattern = raw.raw_pattern
        colours = "".join(desc[c] for c in pattern.flat)
        if colours != "RGGB":
            raise ValueError(f"CFA is {colours}, only RGGB is supported")
        img = raw.raw_image_visible.astype(np.float32)
        black = np.array(raw.black_level_per_channel, np.float32)
        white = float(raw.white_level)
        wb = np.array(raw.camera_whitebalance[:3], np.float64)
        if not wb.any():
            wb = np.array(raw.daylight_whitebalance[:3], np.float64)
        wb = (wb / wb[1]).astype(np.float32)

    H, W = img.shape[0] // 2 * 2, img.shape[1] // 2 * 2
    packed = []
    for (dy, dx), c in zip([(0, 0), (0, 1), (1, 0), (1, 1)], pattern.flat):
        packed.append((img[dy:H:2, dx:W:2] - black[c]) / (white - black[c]))
    return align_planes(packed), wb, read_iso(path), estimate_noise(packed[1], packed[2])


def align_planes(packed):
    """Shifts the packed R, G1, G2, B planes to their quads' centres."""
    planes = []
    for p, (sy, sx) in zip(packed, PLANE_SHIFTS):
        M = np.float32([[1, 0, sx], [0, 1, sy]])  # dst(x, y) = src(x + sx, y + sy)
        shifted = cv2.warpAffine(p, M, (p.shape[1], p.shape[0]),
                                 flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP,
                                 borderMode=cv2.BORDER_REFLECT)
        planes.append(np.clip(shifted, 0, 1))
    return np.stack(planes)


def _clipped_var(x, iterations=4):
    """Variance with 3-sigma clipping (robust to stray detail, unlike a MAD not stepped by the
    RAW's integer levels), corrected for the ~2.7 % a Gaussian loses to the clipping."""
    m, sd = x.mean(), x.std()
    for _ in range(iterations):
        keep = np.abs(x - m) < 3 * sd
        m, sd = x[keep].mean(), x[keep].std()
    return float(sd ** 2 * 1.027)


def estimate_noise(g1, g2, bins=16, min_count=20000, flat_frac=0.1):
    """
    One pixel's noise variance a·s + b at level s, from the greens in the flattest 10 % of
    each level band. Each G1 is compared with the mean of its four diagonal G2 neighbours,
    which cancels linear gradients; in flat areas the difference has 1.25× a pixel's variance.
    Detail only adds variance, so a is the median over bands of variance / level, and b what
    remains in the deep shadows. Per photo this is still pulled up by textured photos; the
    dataset uses a library-wide value per ISO (prepare_dataset.py, noise.json).
    """
    # G1 at native (0, 1); its diagonal G2 neighbours: g2[i, j], g2[i, j+1], g2[i-1, j], g2[i-1, j+1]
    nb = (g2[1:, :-1] + g2[1:, 1:] + g2[:-1, :-1] + g2[:-1, 1:]) / 4
    c = g1[1:, :-1]
    d = c - nb
    s = (c + nb) / 2
    sm = cv2.GaussianBlur(s, (0, 0), 1.5)
    activity = np.abs(cv2.Laplacian(sm, cv2.CV_32F)) + np.hypot(
        cv2.Sobel(sm, cv2.CV_32F, 1, 0), cv2.Sobel(sm, cv2.CV_32F, 0, 1))
    activity = cv2.GaussianBlur(activity, (0, 0), 2)

    levels, variances = [], []
    edges = np.geomspace(0.003, 0.9, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (sm >= lo) & (sm < hi)
        if sel.sum() < min_count:
            continue
        act = activity[sel]
        flat = act <= np.percentile(act, flat_frac * 100)
        levels.append(float(s[sel][flat].mean()))
        variances.append(_clipped_var(d[sel][flat]) / 1.25)
    L, V = np.array(levels), np.array(variances)

    mid = L >= 0.02
    if mid.sum() < 2:
        return None
    a = float(np.median(V[mid] / L[mid]))
    b = float(np.median(V[~mid] - a * L[~mid])) if (~mid).any() else 0.0
    return np.array([a, max(b, 0.0)], np.float32)


def mosaic(planes):
    """(4, P, P) quad-centred planes → (4, P/4, P/4) packed RGGB at twice the pixel size."""
    C, P, _ = planes.shape
    blocks = planes.reshape(C, P // 2, 2, P // 2, 2).mean(axis=(2, 4))
    return np.stack([blocks[0, 0::2, 0::2], blocks[1, 0::2, 1::2],
                     blocks[2, 1::2, 0::2], blocks[3, 1::2, 1::2]])


# Monochrome: the model learns one fixed mix, (R + 2G + B) / 4 of white-balanced camera RGB
# (each quad's mean), in linear light. Other mixes come from scaling the input channels, like
# a colour filter in front of the lens (mix_gains). Training samples random channel gains so
# those inputs are in its range: R and B down to MIX_GAIN_MIN, G down to MIX_G_GAIN_MIN,
# relative to the largest.
MONO_MIX = np.array([0.25, 0.5, 0.25])
MIX_GAIN_MIN = 0.005
MIX_G_GAIN_MIN = 0.2

# Approximate transmission (R, G, B) of black-and-white filters, on camera RGB
FILTERS = {
    "none": (1.0, 1.0, 1.0),
    "yellow": (1.0, 0.9, 0.35),
    "orange": (1.0, 0.6, 0.15),
    "red": (1.0, 0.3, 0.05),
    "green": (0.5, 1.0, 0.4),
    "blue": (0.3, 0.5, 1.0),
}


def luminance_weights(raw_path, packed=None, floor=0.02):
    """
    Weights on white-balanced camera RGB (as-shot white balance) for the photo's luminance.

    CIE Y from the camera matrix needs a negative blue weight in camera RGB (for the OM-5 II,
    about 0.13 R + 1.09 G − 0.23 B), which input gains cannot make. So this returns the
    non-negative weights (each ≥ floor, sum 1) closest to Y on the photo's own colours, from
    `packed` (4, h, w), its white-balanced packed Bayer data. Without it, the exact Y weights.
    On 30 ORFs the non-negative fit is off by 5 % at the median (blues brighter, foliage darker).
    """
    with rawpy.imread(str(raw_path)) as raw:
        cam_xyz = np.array(raw.rgb_xyz_matrix[:3], np.float64)
        wb = np.array(raw.camera_whitebalance[:3], np.float64)
        if not wb.any():
            wb = np.array(raw.daylight_whitebalance[:3], np.float64)
    y = np.linalg.inv(cam_xyz)[1] / (wb / wb[1])  # Y = y · camera RGB = (y / wb) · white-balanced
    y /= y.sum()
    if packed is None:
        return y

    from scipy.optimize import nnls
    rgb = np.stack([packed[0], 0.5 * (packed[1] + packed[2]), packed[3]]).reshape(3, -1).T
    rgb = rgb[:: max(1, len(rgb) // 200_000)].astype(np.float64)
    Y = rgb @ y
    keep = (Y > 0.03) & (rgb.max(1) < 0.95 * rgb.max())
    rgb, Y = rgb[keep], Y[keep]
    # w = floor + v, v ≥ 0; rows weighted by 1/sqrt(Y); a heavy last row holds sum(w) = 1
    s = np.sqrt(Y)[:, None]
    A = np.vstack([rgb / s, 1e3 * np.ones((1, 3))])
    b = np.concatenate([(Y - rgb.sum(1) * floor) / s[:, 0], [1e3 * (1 - 3 * floor)]])
    v, _ = nnls(A, b)
    return floor + v


def mix_gains(mix=None, filter="none", luminance=None):
    """
    Input gains (R, G, B) that make the monochrome model output another mix. Multiply the
    white-balanced packed input by (R, G, G, B) before scaling to its peak and gamma.

    mix:       target weights on white-balanced camera RGB (non-negative), or
    luminance: the base mix to apply `filter` to (MONO_MIX, film-like, or luminance_weights).
    Normalised so a neutral grey keeps its brightness. Warns outside the trained gain range.
    """
    if mix is None:
        if luminance is None:
            raise ValueError("give a mix, or luminance weights for the filter")
        mix = np.asarray(luminance, np.float64) * np.asarray(FILTERS[filter])
    mix = np.asarray(mix, np.float64)
    if (mix < 0).any():
        raise ValueError("negative weights cannot be made by scaling the input")
    k = mix / MONO_MIX
    k /= MONO_MIX @ k
    rel = k / k.max()
    if min(rel[0], rel[2]) < MIX_GAIN_MIN or rel[1] < MIX_G_GAIN_MIN:
        print(f"warning: gains {np.round(k, 4)} are outside the trained range")
    return k.astype(np.float32)


# Centre (y, x) of each colour's site in an RGGB superblock, in target px (a site is 2 × 2)
SITE_CENTRES = [(0.5, 0.5), (0.5, 2.5), (2.5, 0.5), (2.5, 2.5)]


def keep_noise(noise, size):
    """
    The input's per-site noise (4, P/4, P/4; R G1 G2 B) as RGB noise on the target's grid
    (3, P, P): each colour's sites interpolated bilinearly from where they sit, greens averaged.
    What a ×2 image looks like that keeps the RAW's own noise, at its pixel scale.
    """
    planes = []
    for n, (cy, cx) in zip(noise, SITE_CENTRES):
        # dst(x, y) = src((x - cx) / 4, (y - cy) / 4): a site's value lands on its centre
        M = np.float32([[0.25, 0, -cx / 4], [0, 0.25, -cy / 4]])
        planes.append(cv2.warpAffine(n, M, (size, size), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                     borderMode=cv2.BORDER_REFLECT))
    return np.stack([planes[0], 0.5 * (planes[1] + planes[2]), planes[3]])


class BayerPatchDataset(Dataset):
    def __init__(self, patch_dir, patch_size=256, is_train=True, monochrome=False,
                 noise_gain=(0.7, 3.0), blur=(0.5, 3.5), target_blur=0.5, mix_prob=0.7,
                 partial_denoise=0.5):
        """
        patch_dir:  .npz patches from prepare_dataset.py; its parent holds noise.json
        patch_size: target size (a multiple of 32; the packed input is a quarter of it)
        noise_gain: range (log-uniform) of the input's noise, relative to one pixel at the photo's ISO
        blur:       range of the Gaussian sigma (target px) on the input. A downscaled image is
                    crisper per pixel than a native one, where the lens spreads detail: textured
                    areas of real ORFs match sigma 1.25–3.1 (median 2.4, measured against these patches).
        target_blur: the target's sigma as a fraction of the input's: 0 teaches the model to
                    remove all of the lens blur (sharpening, and inventing where it cannot),
                    1 to keep all of it (as soft as the lens, like a faithful ×2)
        monochrome: 1-channel target, MONO_MIX in linear light
        mix_prob:   monochrome only: share of samples with random channel gains (see MONO_MIX)
        partial_denoise: share of samples where the model is to remove only part of the noise
                    (see keep_noise); the rest remove all of it
        Validation samples are deterministic per index (crop, noise, blur, gains).
        Returns (input, target, noise level, target noise): see model.BayerModelBase for the
        noise level; target noise is the target's own expected noise, for train.spectral_loss.
        """
        self.patch_files = sorted(Path(patch_dir).glob("*.npz"))
        if not self.patch_files:
            raise ValueError(f"No .npz patch files found in {patch_dir}")
        if patch_size % 32:
            raise ValueError("patch_size must be a multiple of 32")
        self.patch_size = patch_size
        self.is_train = is_train
        self.monochrome = monochrome
        self.noise_gain = noise_gain
        self.blur = blur
        self.target_blur = target_blur
        self.mix_prob = mix_prob
        self.partial_denoise = partial_denoise
        with open(Path(patch_dir).parent / "noise.json") as f:
            self.noise_iso200 = json.load(f)  # {"a": …, "b": …}: pixel variance a·s + b at ISO 200

    def __len__(self):
        return len(self.patch_files)

    def __getitem__(self, idx):
        rng = np.random.default_rng() if self.is_train else np.random.default_rng(idx)
        data = np.load(self.patch_files[idx])
        planes = data["planes"].astype(np.float32) / 65535.0  # (4, S, S)
        wb = data["wb"]
        iso = float(data["iso"]) or 200.0
        a = self.noise_iso200["a"] * iso / 200
        b = self.noise_iso200["b"] * iso / 200

        # Crop on the 4 px grid of the RGGB superblock (2×2 sites of 2×2 quads)
        P, S = self.patch_size, planes.shape[1]
        if self.is_train:
            y, x = (rng.integers(0, (S - P) // 4 + 1, size=2) * 4)
        else:
            y = x = (S - P) // 8 * 4
        planes = planes[:, y:y + P, x:x + P]

        # The planes are all sampled at quad centres, so flips and turns keep them valid
        if self.is_train:
            planes = np.rot90(planes, k=int(rng.integers(4)), axes=(1, 2))
            if rng.random() < 0.5:
                planes = planes[:, :, ::-1]
            planes = np.ascontiguousarray(planes)

        def blurred(p, sigma):
            if sigma < 0.05:
                return p
            return np.stack([cv2.GaussianBlur(q, (0, 0), sigma, borderType=cv2.BORDER_REFLECT) for q in p])

        sigma = rng.uniform(*self.blur)
        t = blurred(planes, sigma * self.target_blur)
        target = np.stack([t[0], 0.5 * (t[1] + t[2]), t[3]])
        inp = mosaic(blurred(planes, sigma))

        # Noise: the input gets `gain` × one pixel's variance in all. Of the added part, `keep`
        # (in the same units) also goes into the target, so the model learns to remove only
        # `gain - keep`: the level it is told (see the end). The patch's own residual noise
        # cannot be split off, so at most the added part is kept.
        gain = math.exp(rng.uniform(math.log(self.noise_gain[0]), math.log(self.noise_gain[1])))
        added = max(gain - residual_noise(sigma), 0.0)
        keep = 0.0
        if rng.random() < self.partial_denoise:
            keep = min((1 - rng.uniform(0, 1)) * gain, added)
        unit = a * np.clip(inp, 0, None) + b  # one pixel's variance at each site
        n_keep = rng.standard_normal(inp.shape, dtype=np.float32) * np.sqrt(keep * unit)
        n_rest = rng.standard_normal(inp.shape, dtype=np.float32) * np.sqrt((added - keep) * unit)
        inp = np.clip(inp + n_keep + n_rest, 0, 1)
        if keep > 0:
            target = target + keep_noise(n_keep, P)

        # White balance, then clipped highlights made neutral, as upscale_raw.clip_highlights
        # does at inference: where a channel reached the white level, all channels capped at
        # the lowest one's clip level (otherwise a clipped green turns magenta)
        inp = inp * np.array([wb[0], wb[1], wb[1], wb[2]], np.float32)[:, None, None]
        target = target * wb[:, None, None]
        clipped = cv2.dilate((planes >= 0.99).any(axis=0).astype(np.uint8), np.ones((3, 3), np.uint8))
        level = float(wb.min())
        target[:, clipped.astype(bool)] = np.minimum(target[:, clipped.astype(bool)], level)
        clipped_in = clipped.reshape(P // 4, 4, P // 4, 4).max(axis=(1, 3))
        clipped_in = cv2.dilate(clipped_in, np.ones((3, 3), np.uint8)).astype(bool)
        inp[:, clipped_in] = np.minimum(inp[:, clipped_in], level)

        # Monochrome: channel gains for other mixes after that (and after the noise), as at
        # inference. Capping after the gains would put clipped white at the weakest gained
        # channel's level: grey under a mix with little blue.
        if self.monochrome:
            g = self._mix_gains(rng)
            inp = inp * g[[0, 1, 1, 2]][:, None, None]
            target = target * g[:, None, None]
            wb = wb * g

        # The target's own noise (not the kept part): its variance per pixel before the target
        # blur, in raw units times the white balance squared. R and B of a quad are one shifted
        # pixel, G the mean of two; none where highlights were capped.
        level_raw = np.clip(target / wb[:, None, None], 0, None)
        var = (SHIFT_NOISE * (a * level_raw + b) * np.array([1, 0.5, 1], np.float32)[:, None, None]
               * (wb.astype(np.float32) ** 2)[:, None, None])
        var[:, clipped.astype(bool)] = 0

        head = float(max(1.0, inp.max()))
        if self.monochrome:
            target = np.tensordot(MONO_MIX.astype(np.float32), target, axes=1)[None]
            var = np.tensordot((MONO_MIX ** 2).astype(np.float32), var, axes=1)[None]
        # ... and after scaling and gamma, as the variance of the luma the spectral loss uses
        x = np.clip(target / head, 1e-3, 1)
        var = var / head ** 2 * ((1 / GAMMA) * x ** (1 / GAMMA - 1)) ** 2
        if not self.monochrome:
            var = np.tensordot((MONO_MIX ** 2).astype(np.float32), var, axes=1)[None]
        inp = np.power(np.clip(inp / head, 0, 1), 1 / GAMMA).astype(np.float32)
        target = np.power(np.clip(target / head, 0, 1), 1 / GAMMA).astype(np.float32)

        # The model's noise input: log2 of the noise variance to remove, relative to one pixel
        # at ISO 200 (all of it, gain × ISO / 200, unless part is kept)
        noise = torch.tensor(math.log2((gain - keep) * iso / 200), dtype=torch.float32)
        target_noise = {
            "var": torch.from_numpy(var.astype(np.float32)),  # (1, P, P): white noise, before the blur
            "sigma": torch.tensor(sigma * self.target_blur if sigma * self.target_blur >= 0.05 else 0.0),
        }
        return torch.from_numpy(inp), torch.from_numpy(np.ascontiguousarray(target)), noise, target_noise

    def _mix_gains(self, rng):
        """Random channel gains (R, G, B), normalised so a neutral grey keeps its brightness."""
        if rng.random() >= self.mix_prob:
            return np.ones(3, np.float32)
        lo, g_lo = math.log(MIX_GAIN_MIN), math.log(MIX_G_GAIN_MIN)
        g = np.exp([rng.uniform(lo, 0), rng.uniform(g_lo, 0), rng.uniform(lo, 0)])
        return (g / (MONO_MIX @ g)).astype(np.float32)
