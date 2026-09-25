"""Pick/reject flags: the one piece of user-created data.

Stored in their own SQLite file, outside ``data_dir``, keyed by file content
(sha256). Deleting ``data/`` and re-indexing, or moving files, keeps the flags;
editing a file (new content) drops its flag. Unflagged photos have no row.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

FLAGS = ("pick", "reject")

SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
  sha256     TEXT PRIMARY KEY,
  flag       TEXT NOT NULL CHECK (flag IN ('pick', 'reject')),
  source     TEXT,              -- last known location, for humans and recovery
  rel_path   TEXT,
  updated_at REAL NOT NULL
);
"""

def flag_expr(alias: str = "p") -> str:
    """SQL for a photo's flag ('pick', 'reject' or NULL), for catalogue connections
    that attached the selections DB as "sel" (see db.connect / db.connect_readonly)."""
    return f"(SELECT fl.flag FROM sel.flags fl WHERE fl.sha256 = {alias}.sha256)"


FLAG_EXPR = flag_expr("p")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    return conn


def ensure(path: str | Path) -> None:
    """Create the selections DB if missing, so read-only attaches work."""
    connect(path).close()


def set_flags(
    catalogue: sqlite3.Connection, path: str | Path, ops: list[tuple[list[int], str | None]]
) -> dict[int, str | None]:
    """Apply ``[(photo ids, flag or None)]`` atomically, in order.

    Returns each photo's flag from before the change (for undo). Unknown ids
    are ignored."""
    ids = sorted({i for photo_ids, _ in ops for i in photo_ids})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    photos = {
        r["id"]: r
        for r in catalogue.execute(
            f"SELECT id, sha256, source, rel_path FROM photos WHERE id IN ({marks}) AND sha256 != ''",
            ids,
        )
    }
    if not photos:
        return {}
    conn = connect(path)
    try:
        shas = sorted({r["sha256"] for r in photos.values()})
        smarks = ",".join("?" * len(shas))
        current = dict(
            conn.execute(f"SELECT sha256, flag FROM flags WHERE sha256 IN ({smarks})", shas).fetchall()
        )
        previous = {pid: current.get(r["sha256"]) for pid, r in photos.items()}
        now = time.time()
        with conn:
            for photo_ids, flag in ops:
                if flag is not None and flag not in FLAGS:
                    raise ValueError(f"flag must be one of {FLAGS} or null")
                for pid in photo_ids:
                    r = photos.get(pid)
                    if r is None:
                        continue
                    if flag is None:
                        conn.execute("DELETE FROM flags WHERE sha256 = ?", (r["sha256"],))
                    else:
                        conn.execute(
                            """INSERT INTO flags (sha256, flag, source, rel_path, updated_at)
                               VALUES (?, ?, ?, ?, ?)
                               ON CONFLICT (sha256) DO UPDATE SET flag = excluded.flag,
                                 source = excluded.source, rel_path = excluded.rel_path,
                                 updated_at = excluded.updated_at""",
                            (r["sha256"], flag, r["source"], r["rel_path"], now),
                        )
        return previous
    finally:
        conn.close()
