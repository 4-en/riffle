"""Colour and light of a photo, from its preview: for Curate's colour tools.

- brightness: mean luminance (0 = black, 1 = white)
- contrast: RMS contrast, the standard deviation of luminance
- colorfulness: Hasler & Süsstrunk's colourfulness metric, scaled to about 0..1
- hues: a 24-bin hue histogram (15° per bin, bin 0 centred on red), each pixel
  weighted by its chroma so greys do not count; the share of the frame's
  "colour mass" per hue, summing to at most 1 (a grey photo has almost none)
- accents: up to three intense colours that need not take up much of the frame (a
  red balloon in a blue sky): ``accents_of``
"""

from __future__ import annotations

import json

import numpy as np
from PIL import Image

BINS = 24
WIDTH = 360.0 / BINS
SAMPLE_EDGE = 128  # plenty for averages; keeps it fast
ACCENT_CHROMA = 0.25  # a pixel counts towards an accent from this chroma (0..1) …
ACCENT_VALUE = 0.2  # … and this brightness (dark pixels have unreliable hues)
ACCENT_MIN_AREA = 0.002  # an accent covers at least this share of the frame (a few dozen pixels)
ACCENT_FULL_AREA = 0.01  # from this share on, size no longer matters
ACCENT_BASE = 0.12  # the hue covering the most of the frame is its base colour, not an accent, from this share on
ACCENT_LARGE = (0.1, 0.3)  # an accent covering more than the first share counts less, down to a quarter at the second
ACCENT_APART = 45.0  # degrees between two accents of one photo

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


def _sample(im: Image.Image) -> np.ndarray:
    im = im.convert("RGB")
    im.thumbnail((SAMPLE_EDGE, SAMPLE_EDGE))
    return np.asarray(im, dtype=np.float32) / 255.0


def _hue_chroma(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per pixel: hue in degrees (HSV wheel) and chroma (max − min of r, g, b)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    chroma = mx - mn
    safe = np.where(chroma > 1e-6, chroma, 1.0)
    hue = np.where(
        mx == r, ((g - b) / safe) % 6, np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4)
    ) * 60.0
    return hue, chroma


def color_stats(im: Image.Image) -> tuple[float, float, float, bytes]:
    """(brightness, contrast, colorfulness, hues as float32 bytes)."""
    rgb = _sample(im)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    brightness = float(y.mean())
    contrast = float(y.std())

    rg = r - g
    yb = 0.5 * (r + g) - b
    colorfulness = float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))

    hue, chroma = _hue_chroma(rgb)
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


def accents_of(im: Image.Image) -> list[list[float]]:
    """Up to three accent colours: [[hue in degrees, intensity, area], …], strongest first.

    An accent is an intense colour; it need not take up much of the frame. Per hue
    (a 45° window around each histogram bin), the pixels of at least ACCENT_CHROMA:
    intensity is their 90th-percentile chroma, area their share of the frame. The
    score is ``accent_strength``: intensity, lowered for specks and for large areas.
    The hue covering the most of the frame (from ACCENT_BASE) is the photo's base
    colour, not an accent (a blue sky); a small patch that is the only colour (a red
    balloon on grey) still is one. A blue sky with a red balloon: the red; a grey
    photo: none. (On the first library, without the base rule, skies filling a fifth
    of the frame were most of the "blue accents".)"""
    rgb = _sample(im)
    hue, chroma = _hue_chroma(rgb)
    strong = (chroma >= ACCENT_CHROMA) & (rgb.max(axis=2) >= ACCENT_VALUE)
    if not strong.any():
        return []
    h, c = hue[strong], chroma[strong]
    n = chroma.size
    centres = np.arange(BINS) * WIDTH
    windows = [np.abs(((h - centre + 180.0) % 360.0) - 180.0) <= WIDTH * 1.5 for centre in centres]
    areas = np.array([w.sum() / n for w in windows])
    base = centres[int(np.argmax(areas))] if areas.max() >= ACCENT_BASE else None
    found = []
    for centre, near, area in zip(centres, windows, areas):
        if area < ACCENT_MIN_AREA:
            continue
        if base is not None and abs(((centre - base + 180.0) % 360.0) - 180.0) < ACCENT_APART:
            continue
        hues_near, c_near = np.radians(h[near]), c[near]
        intensity = float(np.percentile(c_near, 90))
        score = accent_strength(intensity, area)
        mean = float(np.degrees(np.arctan2((c_near * np.sin(hues_near)).sum(), (c_near * np.cos(hues_near)).sum())) % 360)
        found.append((score, mean, intensity, area))
    found.sort(reverse=True)
    out: list[list[float]] = []
    for _, deg, intensity, area in found:
        if all(abs(((deg - o[0] + 180.0) % 360.0) - 180.0) >= ACCENT_APART for o in out):
            out.append([round(deg, 1), round(intensity, 3), round(float(area), 4)])
        if len(out) == 3:
            break
    return out


def accent_strength(intensity: float, area: float) -> float:
    """How much an accent stands out (0..1): its intensity, less for specks and for
    large areas (a large area is part of the palette more than an accent)."""
    lo, hi = ACCENT_LARGE
    large = 1.0 - 0.75 * min(1.0, max(0.0, (area - lo) / (hi - lo)))
    return intensity * min(1.0, area / ACCENT_FULL_AREA) ** 0.5 * large


def parse_accents(text: str | None) -> list[list[float]]:
    try:
        return [a for a in json.loads(text) if len(a) == 3] if text else []
    except (ValueError, TypeError):
        return []


def accent_affinity(accents: str | list | None, degrees: float) -> float:
    """How strongly a photo has an accent of (roughly) this hue: the strongest of its
    accents within ±30° (a triangular window), by ``accent_strength``."""
    items = parse_accents(accents) if isinstance(accents, str) or accents is None else accents
    best = 0.0
    for deg, intensity, area in items:
        delta = abs(((deg - degrees + 180.0) % 360.0) - 180.0)
        best = max(best, max(0.0, 1.0 - delta / 30.0) * accent_strength(intensity, area))
    return best


def hue_name(degrees: float) -> str:
    """The nearest named hue (HUES)."""
    return min(HUES, key=lambda name: abs(((HUES[name][0] - degrees + 180.0) % 360.0) - 180.0))
