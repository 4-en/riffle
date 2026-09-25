"""SQLite catalogue. Plain SQL; schema version tracked with PRAGMA user_version."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
  id          INTEGER PRIMARY KEY,
  rel_path    TEXT NOT NULL,
  source      TEXT NOT NULL,
  sha256      TEXT NOT NULL,
  size_bytes  INTEGER, mtime REAL,
  width INTEGER, height INTEGER,
  taken_at    TEXT,
  tz_offset   TEXT,
  camera TEXT, lens TEXT,
  lat REAL, lon REAL,
  phash       TEXT,
  dupe_group  INTEGER,
  status      TEXT NOT NULL DEFAULT 'ok',
  error       TEXT,
  UNIQUE (source, rel_path)
);

CREATE TABLE IF NOT EXISTS raws (
  id         INTEGER PRIMARY KEY,
  photo_id   INTEGER REFERENCES photos(id) ON DELETE SET NULL,
  rel_path   TEXT NOT NULL,
  source     TEXT NOT NULL,
  size_bytes INTEGER,
  UNIQUE (source, rel_path)
);

CREATE TABLE IF NOT EXISTS tags (
  id     INTEGER PRIMARY KEY,
  family TEXT NOT NULL,
  name   TEXT NOT NULL,
  UNIQUE (family, name)
);

CREATE TABLE IF NOT EXISTS photo_tags (
  photo_id INTEGER REFERENCES photos(id) ON DELETE CASCADE,
  tag_id   INTEGER REFERENCES tags(id) ON DELETE CASCADE,
  prob     REAL NOT NULL,
  sim      REAL NOT NULL,
  model_id TEXT NOT NULL,
  PRIMARY KEY (photo_id, tag_id, model_id)
);

-- Which photo content (by sha256) is in each model's embedding file.
CREATE TABLE IF NOT EXISTS embedded (
  photo_id INTEGER REFERENCES photos(id) ON DELETE CASCADE,
  model_id TEXT NOT NULL,
  sha256   TEXT NOT NULL,
  PRIMARY KEY (photo_id, model_id)
);

CREATE INDEX IF NOT EXISTS photos_sha ON photos(sha256);
CREATE INDEX IF NOT EXISTS photos_dupe ON photos(dupe_group);
CREATE INDEX IF NOT EXISTS photo_tags_tag ON photo_tags(tag_id, model_id);
CREATE INDEX IF NOT EXISTS raws_photo ON raws(photo_id);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Catalogue schema v{version} is newer than this tool (v{SCHEMA_VERSION})."
        )
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
