"""Exact and near-duplicate grouping by perceptual hash (pHash on previews)."""

from __future__ import annotations

import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import imagehash
import numpy as np
from PIL import Image

from .config import Config
from .colors import color_stats
from .quality import clipping, sharpness

log = logging.getLogger(__name__)


def compute_phashes(conn: sqlite3.Connection, cfg: Config, progress=None) -> int:
    """pHash (for duplicates), sharpness and exposure clipping (for culling), colour
    and light (for Curate), and the layout fingerprint (for Discover) of each preview
    that lacks them. Only what is missing is computed, so adding a measure does not
    redo the others."""
    from .discover import layout_of

    rows = conn.execute(
        """SELECT id, phash IS NULL OR sharpness IS NULL OR clip_highlights IS NULL AS basic,
                  brightness IS NULL AS color, layout IS NULL AS lay
           FROM photos WHERE status = 'ok'
           AND (phash IS NULL OR sharpness IS NULL OR clip_highlights IS NULL OR brightness IS NULL OR layout IS NULL)
           ORDER BY id"""
    ).fetchall()
    def measure(r):
        """The missing measures of one preview (None if it has no preview)."""
        path = cfg.previews_dir / f"{r['id']}.jpg"
        if not path.exists():
            return r, None, None, None
        with Image.open(path) as im:
            basic = (str(imagehash.phash(im)), sharpness(im), *clipping(im)) if r["basic"] else None
            color = color_stats(im) if r["color"] else None
            lay = layout_of(im).astype(np.float32).tobytes() if r["lay"] else None
        return r, basic, color, lay

    # Threads: decoding and most of the measuring release the GIL (4 threads ran
    # about 3x faster than one on the first library; more added little).
    n = 0
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        results = pool.map(measure, rows)
        if progress and rows:
            results = progress(results, total=len(rows), desc="phash")
        for r, basic, color, lay in results:
            if basic is None and color is None and lay is None:
                continue
            if basic:
                conn.execute(
                    "UPDATE photos SET phash = ?, sharpness = ?, clip_highlights = ?, clip_shadows = ? WHERE id = ?",
                    (*basic, r["id"]),
                )
            if color:
                conn.execute(
                    "UPDATE photos SET brightness = ?, contrast = ?, colorfulness = ?, hues = ? WHERE id = ?",
                    (*color, r["id"]),
                )
            if lay:
                conn.execute("UPDATE photos SET layout = ? WHERE id = ?", (lay, r["id"]))
            n += 1
    conn.commit()
    return n


_POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def hamming(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamming distances between two arrays of uint64 hashes, shape (len(a), len(b))."""
    ab = a.astype(">u8").view(np.uint8).reshape(len(a), 8)
    bb = b.astype(">u8").view(np.uint8).reshape(len(b), 8)
    return _POPCOUNT[ab[:, None, :] ^ bb[None, :, :]].sum(axis=2, dtype=np.uint16)


def group_pairs(n: int, pairs: np.ndarray) -> list[int]:
    """Union-find over index pairs; returns a root index per element."""
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in pairs:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return [find(i) for i in range(n)]


def group_duplicates(conn: sqlite3.Connection, cfg: Config, chunk: int = 2048) -> int:
    """Assign dupe_group (lowest photo id of the group) or NULL. Returns number of groups."""
    rows = conn.execute(
        "SELECT id, phash FROM photos WHERE status = 'ok' AND phash IS NOT NULL ORDER BY id"
    ).fetchall()
    conn.execute("UPDATE photos SET dupe_group = NULL")
    if len(rows) < 2:
        conn.commit()
        return 0
    ids = np.array([r["id"] for r in rows], dtype=np.int64)
    hashes = np.array([int(r["phash"], 16) for r in rows], dtype=np.uint64)

    pairs = []
    for start in range(0, len(hashes), chunk):
        d = hamming(hashes[start : start + chunk], hashes)
        i, j = np.nonzero(d <= cfg.phash_max_distance)
        i = i + start
        mask = i < j
        pairs.append(np.stack([i[mask], j[mask]], axis=1))
    roots = group_pairs(len(ids), np.concatenate(pairs))

    sizes: dict[int, int] = {}
    for r in roots:
        sizes[r] = sizes.get(r, 0) + 1
    updates = [(int(ids[r]), int(ids[k])) for k, r in enumerate(roots) if sizes[r] > 1]
    conn.executemany("UPDATE photos SET dupe_group = ? WHERE id = ?", updates)
    conn.commit()
    return sum(1 for s in sizes.values() if s > 1)
