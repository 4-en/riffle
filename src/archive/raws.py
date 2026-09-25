"""Link RAW files to images by case-insensitive filename stem. RAWs are never opened."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .config import Config
from .scan import FoundFile


def _key(folder: str | Path, stem: str) -> tuple[str, str]:
    return os.path.normpath(str(folder)), stem.lower()


def match_raws(conn: sqlite3.Connection, cfg: Config, raws: list[FoundFile]) -> tuple[int, int]:
    """Rebuild the raws table. Returns (matched, unmatched)."""
    # (folder a RAW may live in, stem) -> candidate photo ids, best first.
    lookup: dict[tuple[str, str], list[tuple[int, int]]] = {}
    rows = conn.execute("SELECT id, source, rel_path FROM photos WHERE status != 'missing'")
    for r in rows:
        path = Path(r["source"]) / r["rel_path"]
        stem = path.stem
        for priority, d in enumerate(cfg.raw_search_dirs):
            lookup.setdefault(_key(path.parent / d, stem), []).append((priority, r["id"]))

    conn.execute("DELETE FROM raws")
    matched = unmatched = 0
    for f in raws:
        candidates = lookup.get(_key(f.abs_path.parent, f.abs_path.stem))
        photo_id = min(candidates)[1] if candidates else None
        conn.execute(
            "INSERT INTO raws (photo_id, rel_path, source, size_bytes) VALUES (?, ?, ?, ?)",
            (photo_id, f.rel_path, f.source, f.size),
        )
        if photo_id is None:
            unmatched += 1
        else:
            matched += 1
    conn.commit()
    return matched, unmatched
