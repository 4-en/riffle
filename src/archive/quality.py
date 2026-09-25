"""Simple image quality hints for culling. Not an aesthetic score."""

from __future__ import annotations

import numpy as np
from PIL import Image

SHARPNESS_EDGE = 800
TILE = 50
TOP_FRACTION = 0.05


def sharpness(im: Image.Image) -> float:
    """How sharp the sharpest part of the photo is.

    The Laplacian (fine detail) is computed on a greyscale copy with an 800 px
    long edge (downsampling first keeps sensor noise from looking like detail);
    its variance is taken per 50 px tile, and the sharpest 5% of tiles are
    averaged. Scoring the in-focus region rather than the whole frame keeps a
    sharp subject against a blurred background from scoring low.

    Only meaningful relative to similar shots (a stack): scene content changes
    the absolute value a lot."""
    g = im.convert("L")
    if max(g.size) > SHARPNESS_EDGE:
        g = g.copy()
        g.thumbnail((SHARPNESS_EDGE, SHARPNESS_EDGE), Image.Resampling.BOX)
    a = np.asarray(g, dtype=np.float32)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return 0.0
    lap = a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:] - 4 * a[1:-1, 1:-1]
    h, w = lap.shape
    tile = min(TILE, h, w)
    h, w = h - h % tile, w - w % tile
    tiles = lap[:h, :w].reshape(h // tile, tile, w // tile, tile).var(axis=(1, 3)).ravel()
    k = max(1, int(len(tiles) * TOP_FRACTION))
    return float(np.sort(tiles)[-k:].mean())
