"""Colour and light of a photo, from its preview: for Curate's colour tools.

- brightness: mean luminance (0 = black, 1 = white)
- contrast: RMS contrast, the standard deviation of luminance
- colorfulness: Hasler & Süsstrunk's colourfulness metric, scaled to about 0..1
- hues: a 24-bin hue histogram (15° per bin, bin 0 centred on red), each pixel
  weighted by its chroma so greys do not count; the share of the frame's
  "colour mass" per hue, summing to at most 1 (a grey photo has almost none)
"""

from __future__ import annotations

import numpy as np
from PIL import Image

BINS = 24
WIDTH = 360.0 / BINS
SAMPLE_EDGE = 128  # plenty for averages; keeps it fast

# Named hues for the swatches (degrees on the HSV colour wheel) and their display colour.
HUES = {
    "red": (0, "#e0433a"),
    "orange": (28, "#ee8a2a"),
    "yellow": (52, "#e8c93a"),
    "green": (115, "#4caf50"),
    "teal": (180, "#26a69a"),
    "blue": (215, "#3b7bd8"),
    "purple": (275, "#8e5ad6"),
    "pink": (325, "#e05c9f"),
}


def color_stats(im: Image.Image) -> tuple[float, float, float, bytes]:
    """(brightness, contrast, colorfulness, hues as float32 bytes)."""
    im = im.convert("RGB")
    im.thumbnail((SAMPLE_EDGE, SAMPLE_EDGE))
    rgb = np.asarray(im, dtype=np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    brightness = float(y.mean())
    contrast = float(y.std())

    rg = r - g
    yb = 0.5 * (r + g) - b
    colorfulness = float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))

    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    chroma = mx - mn
    safe = np.where(chroma > 1e-6, chroma, 1.0)
    hue = np.where(
        mx == r, ((g - b) / safe) % 6, np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4)
    ) * 60.0  # degrees
    bins = np.floor(((hue + WIDTH / 2) % 360.0) / WIDTH).astype(int) % BINS  # bin 0 centred on 0°
    hist = np.bincount(bins.ravel(), weights=chroma.ravel(), minlength=BINS)[:BINS] / chroma.size
    return brightness, contrast, colorfulness, hist.astype(np.float32).tobytes()


def dominant_hue(hues: bytes | None) -> tuple[float | None, float]:
    """(the photo's main hue in degrees, its colour mass): the chroma-weighted circular
    mean of the histogram. None for a photo without colour data."""
    if not hues:
        return None, 0.0
    hist = np.frombuffer(hues, dtype=np.float32)
    if len(hist) != BINS or hist.sum() <= 0:
        return None, 0.0
    angles = np.radians(np.arange(BINS) * WIDTH)
    x, y = (hist * np.cos(angles)).sum(), (hist * np.sin(angles)).sum()
    return float(np.degrees(np.arctan2(y, x)) % 360), float(hist.sum())


def hue_affinity(hues: bytes | None, degrees: float) -> float:
    """How much of the frame has (roughly) this hue: the chroma-weighted share
    within ±30° of it, the closest bins counting most (a triangular window)."""
    if not hues:
        return 0.0
    hist = np.frombuffer(hues, dtype=np.float32)
    if len(hist) != BINS:
        return 0.0
    centres = np.arange(BINS) * WIDTH
    delta = np.abs(((centres - degrees + 180.0) % 360.0) - 180.0)
    kernel = np.clip(1.0 - delta / 30.0, 0.0, None)
    return float((hist * kernel).sum())
