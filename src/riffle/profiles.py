"""Profiles: switchable sets of personal data (flags, export history, custom tags).

A profile is one selections database (selections.py). The default profile is the
usual file (``cfg.selections_path``); others live next to it in ``profiles/``,
each naming itself in its ``profile`` table. The active one is remembered per
machine in ``active_profile`` beside them (absent: the default). Everything else
(catalogue, embeddings, settings) is shared, so profiles suit separate projects on
one library.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from . import selections
from .config import Config

DEFAULT = "default"
PARTS = {
    "flags": ["flags"],
    "exported": ["exported"],
    "tags": ["custom_tags", "custom_tag_examples", "custom_tag_negatives"],
    "captions": ["captions", "fixed_tags", "photo_text"],
}


class ProfileError(ValueError):
    """A profile request that cannot be done."""


def _base(cfg: Config) -> Path:
    return Path(cfg.selections_path).parent


def profiles_dir(cfg: Config) -> Path:
    return _base(cfg) / "profiles"


def path_for(cfg: Config, slug: str) -> Path:
    if slug == DEFAULT:
        return Path(cfg.selections_path)
    if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        raise ProfileError("no such profile")
    return profiles_dir(cfg) / f"{slug}.sqlite3"


def _exists(cfg: Config, slug: str) -> bool:
    return slug == DEFAULT or path_for(cfg, slug).is_file()


def active_slug(cfg: Config) -> str:
    try:
        slug = (_base(cfg) / "active_profile").read_text().strip()
    except OSError:
        return DEFAULT
    try:
        return slug if slug and _exists(cfg, slug) else DEFAULT
    except ProfileError:
        return DEFAULT


def active_path(cfg: Config) -> Path:
    return path_for(cfg, active_slug(cfg))


def _name(path: Path, slug: str) -> str:
    conn = selections.connect(path)
    try:
        row = conn.execute("SELECT name FROM profile LIMIT 1").fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else ("Default" if slug == DEFAULT else slug)


def _counts(path: Path) -> dict:
    conn = selections.connect(path)
    try:
        q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "picks": q("SELECT COUNT(*) FROM flags WHERE flag = 'pick'"),
            "rejects": q("SELECT COUNT(*) FROM flags WHERE flag = 'reject'"),
            "exported": q("SELECT COUNT(*) FROM exported"),
            "tags": q("SELECT COUNT(*) FROM custom_tags"),
        }
    finally:
        conn.close()


def list_profiles(cfg: Config) -> list[dict]:
    """The default profile first, then the others by name."""
    active = active_slug(cfg)
    slugs = [p.stem for p in profiles_dir(cfg).glob("*.sqlite3")] if profiles_dir(cfg).is_dir() else []
    out = []
    for slug in [DEFAULT, *slugs]:
        path = path_for(cfg, slug)
        out.append({"slug": slug, "name": _name(path, slug), "path": str(path), "active": slug == active, **_counts(path)})
    return [out[0], *sorted(out[1:], key=lambda p: p["name"].casefold())]


def _clean_name(cfg: Config, name: str, skip: str | None = None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ProfileError("the profile needs a name")
    if any(p["name"].casefold() == name.casefold() and p["slug"] != skip for p in list_profiles(cfg)):
        raise ProfileError(f'a profile named "{name}" already exists')
    return name


def _set_name(path: Path, name: str) -> None:
    conn = selections.connect(path)
    try:
        with conn:
            conn.execute("DELETE FROM profile")
            conn.execute("INSERT INTO profile (name) VALUES (?)", (name,))
    finally:
        conn.close()


def create(cfg: Config, name: str, copy_from: str | None = None, parts: set[str] | None = None) -> str:
    """A new profile, empty or with the chosen parts copied from ``copy_from``. Returns its slug."""
    name = _clean_name(cfg, name)
    if copy_from is not None and not _exists(cfg, copy_from):
        raise ProfileError("no such profile to copy from")
    parts = set(PARTS) if parts is None else set(parts)
    if parts - set(PARTS):
        raise ProfileError(f"parts must be among {', '.join(PARTS)}")
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "profile"
    slug, k = base, 2
    while slug == DEFAULT or path_for(cfg, slug).exists():
        slug, k = f"{base}-{k}", k + 1
    path = path_for(cfg, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = selections.connect(path)
    try:
        if copy_from is not None and parts:
            selections.ensure(path_for(cfg, copy_from))  # an older file gains the newer tables first
            conn.execute("ATTACH DATABASE ? AS src", (str(path_for(cfg, copy_from)),))
            with conn:
                for part in parts:
                    for table in PARTS[part]:
                        conn.execute(f"INSERT INTO {table} SELECT * FROM src.{table}")
            conn.execute("DETACH DATABASE src")
    finally:
        conn.close()
    _set_name(path, name)
    return slug


def rename(cfg: Config, slug: str, name: str) -> None:
    if not _exists(cfg, slug):
        raise ProfileError("no such profile")
    _set_name(path_for(cfg, slug), _clean_name(cfg, name, skip=slug))


def delete(cfg: Config, slug: str) -> Path:
    """Move a profile's file to profiles/deleted/ (it is user data). Not the
    default or the active profile. Returns where it went."""
    if slug == DEFAULT:
        raise ProfileError("the default profile cannot be deleted")
    if not _exists(cfg, slug):
        raise ProfileError("no such profile")
    if slug == active_slug(cfg):
        raise ProfileError("switch to another profile before deleting this one")
    path = path_for(cfg, slug)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # everything into the main file
    finally:
        conn.close()
    target = profiles_dir(cfg) / "deleted" / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    path.rename(target)
    for extra in ("-wal", "-shm"):
        Path(f"{path}{extra}").unlink(missing_ok=True)
    return target


def switch(cfg: Config, slug: str) -> Path:
    """Make ``slug`` the active profile; returns its file."""
    if not _exists(cfg, slug):
        raise ProfileError("no such profile")
    marker = _base(cfg) / "active_profile"
    if slug == DEFAULT:
        marker.unlink(missing_ok=True)
    else:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(slug + "\n")
    return path_for(cfg, slug)
