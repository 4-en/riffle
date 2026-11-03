"""Stacks: bursts and near-identical shots, for choosing the best of several.

Consecutive photos (by capture time) are linked when they were taken at most
``max_gap_seconds`` apart and their CLIP embeddings have cosine similarity of at
least ``min_similarity``. Links are merged with the pHash duplicate groups, and
each connected set of two or more photos becomes a stack, identified by its
lowest photo id.
"""

from __future__ import annotations

import calendar
import sqlite3
import time

import numpy as np

from .config import Config
from .dupes import group_pairs
from .embed import load_embeddings


def exif_seconds(taken_at: str | None) -> float | None:
    """EXIF "YYYY:MM:DD HH:MM:SS" as seconds, or None. Only differences are used,
    so the (unknown) timezone does not matter."""
    if not taken_at:
        return None
    try:
        return float(calendar.timegm(time.strptime(taken_at[:19], "%Y:%m:%d %H:%M:%S")))
    except ValueError:
        return None


def compute_stacks(conn: sqlite3.Connection, cfg: Config) -> tuple[int, int]:
    """Recompute photos.stack_id. Returns (stacks, photos in stacks)."""
    rows = conn.execute(
        """SELECT id, taken_at, dupe_group FROM photos WHERE status = 'ok'
           ORDER BY taken_at IS NULL, taken_at, source, rel_path"""
    ).fetchall()
    conn.execute("UPDATE photos SET stack_id = NULL")
    if len(rows) < 2:
        conn.commit()
        return 0, 0

    E, ids = load_embeddings(cfg)
    row_of = {int(pid): i for i, pid in enumerate(ids)}
    index = {r["id"]: i for i, r in enumerate(rows)}
    pairs: list[tuple[int, int]] = []

    gap = cfg.stacks.max_gap_seconds
    for (a, b) in zip(rows, rows[1:]):
        ta, tb = exif_seconds(a["taken_at"]), exif_seconds(b["taken_at"])
        ra, rb = row_of.get(a["id"]), row_of.get(b["id"])
        if ta is None or tb is None or ra is None or rb is None:
            continue
        if tb - ta <= gap and float(E[ra] @ E[rb]) >= cfg.stacks.min_similarity:
            pairs.append((index[a["id"]], index[b["id"]]))

    first_of_group: dict[int, int] = {}
    for r in rows:
        if r["dupe_group"] is not None:
            first = first_of_group.setdefault(r["dupe_group"], index[r["id"]])
            if first != index[r["id"]]:
                pairs.append((first, index[r["id"]]))

    roots = group_pairs(len(rows), np.array(pairs, dtype=np.int64).reshape(-1, 2))
    members: dict[int, list[int]] = {}
    for i, root in enumerate(roots):
        members.setdefault(root, []).append(rows[i]["id"])
    updates = [
        (min(group), pid) for group in members.values() if len(group) > 1 for pid in group
    ]
    conn.executemany("UPDATE photos SET stack_id = ? WHERE id = ?", updates)
    conn.commit()
    return sum(1 for g in members.values() if len(g) > 1), len(updates)
