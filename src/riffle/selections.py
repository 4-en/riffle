"""Pick/reject flags, export history, and custom tags: the user-created data.

Stored in their own SQLite file, outside ``data_dir``, keyed by file content
(sha256). Deleting the derived data and re-indexing, or moving files, keeps them;
editing a file (new content) drops them. Unflagged photos have no flags row;
photos never exported have no exported row. Flag and exported are independent.
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

-- v2: which photos have been exported (independent of the flag).
CREATE TABLE IF NOT EXISTS exported (
  sha256      TEXT PRIMARY KEY,
  first_at    REAL NOT NULL,
  last_at     REAL NOT NULL,
  times       INTEGER NOT NULL DEFAULT 1,
  last_folder TEXT,             -- where the last export went
  source      TEXT,             -- last known location, for humans and recovery
  rel_path    TEXT
);

-- v3: tags taught by example photos (custom_tags.py).
CREATE TABLE IF NOT EXISTS custom_tags (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
  strictness  TEXT NOT NULL DEFAULT 'normal' CHECK (strictness IN ('strict', 'normal', 'loose')),
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS custom_tag_examples (
  tag_id      INTEGER NOT NULL REFERENCES custom_tags(id),
  sha256      TEXT NOT NULL,
  source      TEXT,             -- last known location, for humans and recovery
  rel_path    TEXT,
  added_at    REAL NOT NULL,
  PRIMARY KEY (tag_id, sha256)
);
"""

STRICTNESS = ("strict", "normal", "loose")

def flag_expr(alias: str = "p") -> str:
    """SQL for a photo's flag ('pick', 'reject' or NULL), for catalogue connections
    that attached the selections DB as "sel" (see db.connect / db.connect_readonly)."""
    return f"(SELECT fl.flag FROM sel.flags fl WHERE fl.sha256 = {alias}.sha256)"


FLAG_EXPR = flag_expr("p")


def exported_expr(alias: str = "p") -> str:
    """SQL: 1 if the photo was exported before, else 0 (needs "sel" attached)."""
    return f"EXISTS (SELECT 1 FROM sel.exported ex WHERE ex.sha256 = {alias}.sha256)"


EXPORTED_EXPR = exported_expr("p")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA user_version = 3")
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


def clear_flags(
    catalogue: sqlite3.Connection, path: str | Path, photo_ids: list[int] | None = None
) -> dict[int, str]:
    """Unflag the given photos, or everything (including flags of files no longer
    in the library) when ``photo_ids`` is None. Returns the previous flags of the
    affected catalogue photos, for undo."""
    conn = connect(path)
    try:
        flags = dict(conn.execute("SELECT sha256, flag FROM flags").fetchall())
        rows = catalogue.execute("SELECT id, sha256 FROM photos WHERE sha256 != ''").fetchall()
        if photo_ids is not None:
            wanted = set(photo_ids)
            rows = [r for r in rows if r["id"] in wanted]
        previous = {r["id"]: flags[r["sha256"]] for r in rows if r["sha256"] in flags}
        with conn:
            if photo_ids is None:
                conn.execute("DELETE FROM flags")
            else:
                conn.executemany(
                    "DELETE FROM flags WHERE sha256 = ?", [(r["sha256"],) for r in rows]
                )
        return previous
    finally:
        conn.close()


def _photos(catalogue: sqlite3.Connection, photo_ids: list[int] | None):
    rows = catalogue.execute("SELECT id, sha256, source, rel_path FROM photos WHERE sha256 != ''").fetchall()
    if photo_ids is not None:
        wanted = set(photo_ids)
        rows = [r for r in rows if r["id"] in wanted]
    return rows


def mark_exported(catalogue: sqlite3.Connection, path: str | Path, photo_ids: list[int], folder: str) -> int:
    """Record that these photos were exported (to ``folder``). Returns how many."""
    rows = _photos(catalogue, photo_ids)
    now = time.time()
    conn = connect(path)
    try:
        with conn:
            conn.executemany(
                """INSERT INTO exported (sha256, first_at, last_at, times, last_folder, source, rel_path)
                   VALUES (?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT (sha256) DO UPDATE SET last_at = excluded.last_at,
                     times = exported.times + 1, last_folder = excluded.last_folder,
                     source = excluded.source, rel_path = excluded.rel_path""",
                [(r["sha256"], now, now, folder, r["source"], r["rel_path"]) for r in rows],
            )
        return len(rows)
    finally:
        conn.close()


def export_info(path: str | Path, sha256: str) -> dict | None:
    conn = connect(path)
    try:
        r = conn.execute(
            "SELECT first_at, last_at, times, last_folder FROM exported WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def clear_exported(catalogue: sqlite3.Connection, path: str | Path, photo_ids: list[int] | None = None) -> int:
    """Forget the export history of these photos, or all of it. Returns how many rows."""
    conn = connect(path)
    try:
        with conn:
            if photo_ids is None:
                return conn.execute("DELETE FROM exported").rowcount
            shas = [(r["sha256"],) for r in _photos(catalogue, photo_ids)]
            return sum(conn.execute("DELETE FROM exported WHERE sha256 = ?", s).rowcount for s in shas)
    finally:
        conn.close()


# ---- custom tags --------------------------------------------------------------


class TagError(ValueError):
    """A custom tag request that cannot be done (duplicate or empty name, unknown tag)."""


def list_tags(path: str | Path) -> list[dict]:
    """Every custom tag with its examples' content hashes, by name."""
    conn = connect(path)
    try:
        tags = [dict(r) for r in conn.execute("SELECT * FROM custom_tags ORDER BY name COLLATE NOCASE")]
        examples: dict[int, list[str]] = {}
        for r in conn.execute("SELECT tag_id, sha256 FROM custom_tag_examples ORDER BY added_at, sha256"):
            examples.setdefault(r["tag_id"], []).append(r["sha256"])
        for t in tags:
            t["examples"] = examples.get(t["id"], [])
        return tags
    finally:
        conn.close()


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise TagError("the tag needs a name")
    return name


def _add_examples(conn: sqlite3.Connection, catalogue: sqlite3.Connection, tag_id: int, photo_ids: list[int], now: float) -> None:
    conn.executemany(
        """INSERT INTO custom_tag_examples (tag_id, sha256, source, rel_path, added_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (tag_id, sha256) DO UPDATE SET source = excluded.source, rel_path = excluded.rel_path""",
        [(tag_id, r["sha256"], r["source"], r["rel_path"], now) for r in _photos(catalogue, photo_ids)],
    )


def create_tag(catalogue: sqlite3.Connection, path: str | Path, name: str, photo_ids: list[int], strictness: str = "normal") -> int:
    """A new tag from example photos; returns its id."""
    name = _clean_name(name)
    if strictness not in STRICTNESS:
        raise TagError(f"strictness must be one of {', '.join(STRICTNESS)}")
    if not _photos(catalogue, photo_ids):
        raise TagError("choose at least one photo as an example")
    conn = connect(path)
    try:
        now = time.time()
        with conn:
            try:
                tag_id = conn.execute(
                    "INSERT INTO custom_tags (name, strictness, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (name, strictness, now, now),
                ).lastrowid
            except sqlite3.IntegrityError:
                raise TagError(f'a tag named "{name}" already exists')
            _add_examples(conn, catalogue, tag_id, photo_ids, now)
        return tag_id
    finally:
        conn.close()


def update_tag(
    catalogue: sqlite3.Connection,
    path: str | Path,
    tag_id: int,
    *,
    name: str | None = None,
    strictness: str | None = None,
    add: list[int] | None = None,
    remove: list[int] | None = None,
) -> None:
    """Rename, change strictness, add or remove example photos."""
    if strictness is not None and strictness not in STRICTNESS:
        raise TagError(f"strictness must be one of {', '.join(STRICTNESS)}")
    conn = connect(path)
    try:
        now = time.time()
        with conn:
            if conn.execute("SELECT 1 FROM custom_tags WHERE id = ?", (tag_id,)).fetchone() is None:
                raise TagError("no such tag")
            if name is not None:
                try:
                    conn.execute("UPDATE custom_tags SET name = ? WHERE id = ?", (_clean_name(name), tag_id))
                except sqlite3.IntegrityError:
                    raise TagError(f'a tag named "{_clean_name(name)}" already exists')
            if strictness is not None:
                conn.execute("UPDATE custom_tags SET strictness = ? WHERE id = ?", (strictness, tag_id))
            if add:
                _add_examples(conn, catalogue, tag_id, add, now)
            if remove:
                conn.executemany(
                    "DELETE FROM custom_tag_examples WHERE tag_id = ? AND sha256 = ?",
                    [(tag_id, r["sha256"]) for r in _photos(catalogue, remove)],
                )
            conn.execute("UPDATE custom_tags SET updated_at = ? WHERE id = ?", (now, tag_id))
    finally:
        conn.close()


def delete_tag(path: str | Path, tag_id: int) -> bool:
    conn = connect(path)
    try:
        with conn:
            conn.execute("DELETE FROM custom_tag_examples WHERE tag_id = ?", (tag_id,))
            return conn.execute("DELETE FROM custom_tags WHERE id = ?", (tag_id,)).rowcount > 0
    finally:
        conn.close()
