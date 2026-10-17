"""Where things live, following each platform's conventions (via platformdirs).

On Linux, honouring the XDG variables:

- settings (config.yaml, vocabulary.yaml): ``~/.config/imgmind``
- user data (selections.sqlite3, the pick/reject flags): ``~/.local/share/imgmind``
- derived data (catalogue, thumbnails, previews, embeddings): ``~/.cache/imgmind``,
  safe to delete and rebuilt by indexing

macOS uses ``~/Library/Application Support/imgmind`` and ``~/Library/Caches/imgmind``;
Windows ``%APPDATA%\\imgmind`` (settings and flags, which roam with the user
profile) and ``%LOCALAPPDATA%\\imgmind\\Cache``.
"""

from __future__ import annotations

import os
import shutil
import sys
from importlib import resources
from pathlib import Path

import platformdirs

APP = "imgmind"


LEGACY_APP = "riffle"  # the app's former name; its folders are moved over on first use


def _migrated(func) -> Path:
    """The folder for ``APP``; if only the one for the former name exists, move it
    over first, so flags, tags, settings and the index survive the rename."""
    new = Path(func(APP, appauthor=False, roaming=True) if func is not platformdirs.user_cache_dir
               else func(APP, appauthor=False))
    old = Path(func(LEGACY_APP, appauthor=False, roaming=True) if func is not platformdirs.user_cache_dir
               else func(LEGACY_APP, appauthor=False))
    if not new.exists() and old.is_dir():
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
        except OSError:
            return old  # could not move (permissions, a running copy): keep using the old one
    return new


# appauthor=False: no extra "author" folder on Windows (it would be imgmind\\imgmind).
def config_dir() -> Path:
    return _migrated(platformdirs.user_config_dir)


def data_dir() -> Path:
    return _migrated(platformdirs.user_data_dir)


def cache_dir() -> Path:
    return _migrated(platformdirs.user_cache_dir)


def default_file(name: str) -> Path:
    """A file shipped with the package (``defaults/``): config template, vocabulary."""
    return Path(str(resources.files("imgmind") / "defaults" / name))


# The standalone app runs the model on the CPU (see packaging/), so it starts with
# the faster model; the pip install keeps the default. Both are ordinary settings.
STANDALONE_EDITS = [
    ("  name: ViT-L-14-quickgelu\n  pretrained: dfn2b\n",
     "  # The downloaded app starts with the faster model (it runs on the CPU).\n"
     "  name: ViT-B-16\n  pretrained: dfn2b\n"),
    ("  min_similarity: 0.92\n", "  min_similarity: 0.90   # for ViT-B-16 (0.92 for ViT-L-14)\n"),
]


def standalone_defaults(template: str) -> str:
    """The config template with the standalone app's defaults (the faster model)."""
    if all(old in template for old, _ in STANDALONE_EDITS):
        for old, new in STANDALONE_EDITS:
            template = template.replace(old, new, 1)
    return template


def read_text(path: str | Path) -> str:
    """A text file the user may have edited (config, vocabulary): UTF-8, which ImgMind
    writes, else the system's encoding (e.g. saved by an old Windows editor).
    Without an explicit encoding Python uses the system's, cp1252 on Windows, which
    garbles or rejects UTF-8 (a folder or tag with ü or 出口)."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return Path(path).read_text(errors="replace")


def find_config(explicit: str | Path | None = None) -> Path:
    """The config file to use: ``--config``, else ``$IMGMIND_CONFIG``, else
    ``./config.yaml`` if one exists (a project-local setup), else the user's
    config, created from the template on first run."""
    for candidate in (explicit, os.environ.get("IMGMIND_CONFIG") or os.environ.get("RIFFLE_CONFIG")):
        if candidate:
            return Path(candidate).expanduser().resolve()
    local = Path("config.yaml")
    if local.is_file():
        return local.resolve()
    path = config_dir() / "config.yaml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        template = default_file("config.yaml").read_text(encoding="utf-8")
        if getattr(sys, "frozen", False):
            template = standalone_defaults(template)
        path.write_text(template, encoding="utf-8")
        vocabulary = path.parent / "vocabulary.yaml"
        if not vocabulary.exists():
            shutil.copyfile(default_file("vocabulary.yaml"), vocabulary)
    return path
