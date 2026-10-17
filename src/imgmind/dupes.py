"""Exact and near-duplicate grouping by perceptual hash (pHash on previews)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import imagehash
import numpy as np
from PIL import Image

from .config import Config
from .colors import accents_of, color_stats
from .quality import clipping, sharpness

log = logging.getLogger(__name__)


def compute_phashes(conn: sqlite3.Connection, cfg: Config, progress=None) -> int:
    """pHash (for duplicates), sharpness and exposure clipping (for culling), colour
    and light and accent colours (for Curate and Discover), and the layout fingerprint (for Discover) of each preview
    that lacks them. Only what is missing is computed, so adding a measure does not
    redo the others."""
    from .discover import layout_of

    rows = conn.execute(
        """SELECT id, phash IS NULL OR sharpness IS NULL OR clip_highlights IS NULL AS basic,
                  brightness IS NULL AS color, layout IS NULL AS lay, accents IS NULL AS acc
           FROM photos WHERE status = 'ok'
           AND (phash IS NULL OR sharpness IS NULL OR clip_highlights IS NULL OR brightness IS NULL
                OR layout IS NULL OR accents IS NULL)
           ORDER BY id"""
    ).fetchall()
    def measure(r):
        """The missing measures of one preview (None if it has no preview)."""
        path = cfg.previews_dir / f"{r['id']}.jpg"
        if not path.exists():
            return r, None, None, None, None
        with Image.open(path) as im:
            basic = (str(imagehash.phash(im)), sharpness(im), *clipping(im)) if r["basic"] else None
            color = color_stats(im) if r["color"] else None
            lay = layout_of(im).astype(np.float32).tobytes() if r["lay"] else None
            acc = json.dumps(accents_of(im)) if r["acc"] else None
        return r, basic, color, lay, acc

    # Threads: decoding and most of the measuring release the GIL (4 threads ran
    # about 3x faster than one on the first library; more added little).
    n = 0
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        results = pool.map(measure, rows)
        if progress and rows:
            results = progress(results, total=len(rows), desc="phash")
        for r, basic, color, lay, acc in results:
            if basic is None and color is None and lay is None and acc is None:
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
            if acc is not None:
                conn.execute("UPDATE photos SET accents = ? WHERE id = ?", (acc, r["id"]))
            n += 1
    conn.commit()
    return n


_POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def hamming(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamming distances between two arrays of uint64 hashes, shape (len(a), len(b))."""
    x = a.astype(np.uint64)[:, None] ^ b.astype(np.uint64)[None, :]
    if hasattr(np, "bitwise_count"):  # numpy 2: a popcount per 64-bit word
        return np.bitwise_count(x)
    return _POPCOUNT[x.astype(">u8").view(np.uint8).reshape(*x.shape, 8)].sum(axis=2, dtype=np.uint16)


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


def group_duplicates(conn: sqlite3.Connection, cfg: Config, chunk: int = 1024) -> int:
    """Assign dupe_group (lowest photo id of the group) or NULL. Returns number of groups.

    A pair is a duplicate when its perceptual hashes are close and, when both photos are
    embedded, CLIP rates them at least as alike as a stack (``stacks.min_similarity``,
    fitted to the model). A 64-bit hash alone matched unrelated flat, low-detail images
    in a large library (95,000 images: 29 % of the pairs less than 0.9 alike, groups
    chained across folders up to 48 images); with CLIP's agreement, groups spanning
    folders fell from 41 to 11 (copies of one image in two folders)."""
    import hashlib

    rows = conn.execute(
        "SELECT id, phash FROM photos WHERE status = 'ok' AND phash IS NOT NULL ORDER BY id"
    ).fetchall()
    # Nothing to redo when the hashes, the embeddings and the thresholds are as last time
    # (comparing every pair takes a while in a large library).
    model_id = cfg.model.model_id
    sig = hashlib.sha1()
    sig.update(f"{model_id} {cfg.stacks.min_similarity} {cfg.phash_max_distance}\n".encode())
    sig.update(",".join(f"{r['id']}:{r['phash']}" for r in rows).encode())
    sig.update(",".join(f"{r[0]}:{r[1]}" for r in conn.execute(
        "SELECT photo_id, sha256 FROM embedded WHERE model_id = ? ORDER BY photo_id", (model_id,))).encode())
    from .embed import embedding_paths

    for path in embedding_paths(cfg, model_id):
        if path.exists():
            st = path.stat()
            sig.update(f"{path.name} {st.st_size} {st.st_mtime_ns}\n".encode())
    sig = sig.hexdigest()
    last = conn.execute("SELECT value FROM meta WHERE key = 'dupes'").fetchone()
    if last is not None and last[0] == sig:
        return conn.execute(
            "SELECT COUNT(DISTINCT dupe_group) FROM photos WHERE status = 'ok' AND dupe_group IS NOT NULL"
        ).fetchone()[0]

    ids = np.array([r["id"] for r in rows], dtype=np.int64)
    hashes = np.array([int(r["phash"], 16) for r in rows], dtype=np.uint64)

    # Each hash against those after it (half of all pairs).
    pairs = [np.zeros((0, 2), np.int64)]
    for start in range(0, len(hashes), chunk):
        d = hamming(hashes[start : start + chunk], hashes[start:])
        i, j = np.nonzero(d <= cfg.phash_max_distance)
        mask = i < j
        pairs.append(np.stack([i[mask] + start, j[mask] + start], axis=1))
    pairs = np.concatenate(pairs)
    if len(pairs):
        from .embed import load_embeddings

        try:
            E, E_ids = load_embeddings(cfg)
        except (OSError, ValueError):
            E, E_ids = np.zeros((0, 0)), np.zeros(0, np.int64)
        row = {int(i): k for k, i in enumerate(E_ids)}
        a = np.array([row.get(int(ids[i]), -1) for i in pairs[:, 0]])
        b = np.array([row.get(int(ids[j]), -1) for j in pairs[:, 1]])
        both = (a >= 0) & (b >= 0)
        alike = np.ones(len(pairs), bool)
        if both.any():
            alike[both] = (E[a[both]] * E[b[both]]).sum(axis=1) >= cfg.stacks.min_similarity
        pairs = pairs[alike]
    roots = group_pairs(len(ids), pairs)

    sizes: dict[int, int] = {}
    for r in roots:
        sizes[r] = sizes.get(r, 0) + 1
    groups = {int(ids[k]): int(ids[r]) for k, r in enumerate(roots) if sizes[r] > 1}
    # Written at the end, and only what changed: the catalogue stays writable meanwhile.
    before = {r[0]: r[1] for r in conn.execute("SELECT id, dupe_group FROM photos WHERE dupe_group IS NOT NULL")}
    updates = [(g, pid) for pid, g in groups.items() if before.get(pid) != g]
    updates += [(None, pid) for pid in before if pid not in groups]
    with conn:
        conn.executemany("UPDATE photos SET dupe_group = ? WHERE id = ?", updates)
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('dupes', ?)", (sig,))
    return sum(1 for s in sizes.values() if s > 1)
