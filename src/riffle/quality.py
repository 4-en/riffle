"""Simple image quality hints for culling. Not an aesthetic score."""

from __future__ import annotations

import numpy as np
from PIL import Image

SHARPNESS_EDGE = 800
CLIP_HIGH = 250  # a pixel is blown when all three channels reach this (near-white)...
CLIP_LOW = 5  # ...and crushed when all three are at most this (near-black)
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


def clipping(im: Image.Image) -> tuple[float, float]:
    """(blown highlights, crushed shadows) as fractions of the frame.

    Only near-white / near-black pixels count, i.e. all three channels at the
    limit: a saturated colour (a yellow flower with red and green at 255) has
    lost nothing and is not flagged."""
    a = np.asarray(im.convert("RGB"), dtype=np.uint8)
    if a.size == 0:
        return 0.0, 0.0
    return float((a.min(axis=2) >= CLIP_HIGH).mean()), float((a.max(axis=2) <= CLIP_LOW).mean())


# Suggested keeper: weights of the three hints, each relative to the other photos compared.
WEIGHTS = {"sharpness": 0.5, "quality": 0.3, "exposure": 0.2}
FULL_PENALTY_BLOWN, FULL_PENALTY_CRUSHED = 0.10, 0.25  # this much clipping scores exposure 0


def keeper_scores(
    photos: list[dict], quality: dict[int, float] | None = None
) -> tuple[int | None, dict[int, dict[str, float]]]:
    """Rank similar photos: sharpness (relative to the sharpest), exposure (penalised
    by clipping; blown highlights weigh more than crushed shadows), and optionally a
    CLIP quality score (relative within the set). Returns (suggested id, scores);
    every component is 0..1."""
    if not photos:
        return None, {}
    max_sharp = max((p.get("sharpness") or 0) for p in photos)
    q = quality or {}
    q_vals = [q[p["id"]] for p in photos if p["id"] in q]
    q_lo, q_hi = (min(q_vals), max(q_vals)) if q_vals else (0.0, 0.0)
    weights = WEIGHTS if q_vals else {"sharpness": 0.7, "exposure": 0.3}

    scores = {}
    for p in photos:
        s = {
            "sharpness": (p.get("sharpness") or 0) / max_sharp if max_sharp > 0 else 1.0,
            "exposure": 1.0 - min(
                1.0,
                (p.get("clip_highlights") or 0) / FULL_PENALTY_BLOWN
                + (p.get("clip_shadows") or 0) / FULL_PENALTY_CRUSHED,
            ),
        }
        if q_vals:
            s["quality"] = (q[p["id"]] - q_lo) / (q_hi - q_lo) if q_hi > q_lo and p["id"] in q else 1.0
        s["total"] = sum(weights[k] * s[k] for k in weights)
        scores[p["id"]] = s
    return max(scores, key=lambda i: scores[i]["total"]), scores
