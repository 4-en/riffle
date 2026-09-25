"""Where things live, following each platform's conventions (via platformdirs).

On Linux, honouring the XDG variables:

- settings (config.yaml, vocabulary.yaml): ``~/.config/photo-archive``
- user data (selections.sqlite3, the pick/reject flags): ``~/.local/share/photo-archive``
- derived data (catalogue, thumbnails, previews, embeddings): ``~/.cache/photo-archive``,
  safe to delete and rebuilt by indexing

macOS uses ``~/Library/Application Support`` and ``~/Library/Caches``; Windows
``%APPDATA%`` and ``%LOCALAPPDATA%``.
"""

from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path

import platformdirs

APP = "photo-archive"


def config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP))


def data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP))


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir(APP))


def default_file(name: str) -> Path:
    """A file shipped with the package (``defaults/``): config template, vocabulary."""
    return Path(str(resources.files("archive") / "defaults" / name))


def find_config(explicit: str | Path | None = None) -> Path:
    """The config file to use: ``--config``, else ``$ARCHIVE_CONFIG``, else
    ``./config.yaml`` if one exists (a project-local setup), else the user's
    config, created from the template on first run."""
    for candidate in (explicit, os.environ.get("ARCHIVE_CONFIG")):
        if candidate:
            return Path(candidate).expanduser().resolve()
    local = Path("config.yaml")
    if local.is_file():
        return local.resolve()
    path = config_dir() / "config.yaml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(default_file("config.yaml"), path)
        vocabulary = path.parent / "vocabulary.yaml"
        if not vocabulary.exists():
            shutil.copyfile(default_file("vocabulary.yaml"), vocabulary)
    return path
