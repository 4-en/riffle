"""Exact and near-duplicate grouping by perceptual hash (pHash on previews)."""

from __future__ import annotations

import logging
import sqlite3

import imagehash
import numpy as np
from PIL import Image

from .config import Config
from .quality import sharpness

log = logging.getLogger(__name__)


def compute_phashes(conn: sqlite3.Connection, cfg: Config, progress=None) -> int:
    """pHash (for duplicates) and sharpness (for culling) of each preview that lacks them."""
    rows = conn.execute(
        "SELECT id FROM photos WHERE status = 'ok' AND (phash IS NULL OR sharpness IS NULL) ORDER BY id"
    ).fetchall()
    it = progress(rows, desc="phash") if progress and rows else rows
    n = 0
    for r in it:
        path = cfg.previews_dir / f"{r['id']}.jpg"
        if not path.exists():
            continue
        with Image.open(path) as im:
            h = str(imagehash.phash(im))
            s = sharpness(im)
        conn.execute("UPDATE photos SET phash = ?, sharpness = ? WHERE id = ?", (h, s, r["id"]))
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
