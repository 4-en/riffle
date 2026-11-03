"""Where things live, following each platform's conventions (via platformdirs).

On Linux, honouring the XDG variables:

- settings (config.yaml, vocabulary.yaml): ``~/.config/deep-gallery``
- user data (selections.sqlite3, the pick/reject flags): ``~/.local/share/deep-gallery``
- derived data (catalogue, thumbnails, previews, embeddings): ``~/.cache/deep-gallery``,
  safe to delete and rebuilt by indexing

macOS uses ``~/Library/Application Support/deep-gallery`` and ``~/Library/Caches/deep-gallery``;
Windows ``%APPDATA%\\deep-gallery`` (settings and flags, which roam with the user
profile) and ``%LOCALAPPDATA%\\deep-gallery\\Cache``.
"""

from __future__ import annotations

import os
import shutil
import sys
from importlib import resources
from pathlib import Path

import platformdirs

APP = "deep-gallery"


LEGACY_APPS = ("imgmind", "riffle")  # former app names; folders are moved over on first use


def _migrated(func) -> Path:
    """The folder for ``APP``; if only one for a former name exists, move it
    over first, so flags, tags, settings and the index survive the rename."""
    new = Path(func(APP, appauthor=False, roaming=True) if func is not platformdirs.user_cache_dir
               else func(APP, appauthor=False))
    if not new.exists():
        for legacy in LEGACY_APPS:
            old = Path(func(legacy, appauthor=False, roaming=True) if func is not platformdirs.user_cache_dir
                       else func(legacy, appauthor=False))
            if old.is_dir():
                try:
                    new.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old), str(new))
                    break
                except OSError:
                    return old  # could not move (permissions, a running copy): keep using the old one
    return new


# appauthor=False: no extra "author" folder on Windows (it would be deep-gallery\\deep-gallery).
def config_dir() -> Path:
    return _migrated(platformdirs.user_config_dir)


def data_dir() -> Path:
    return _migrated(platformdirs.user_data_dir)


def cache_dir() -> Path:
    return _migrated(platformdirs.user_cache_dir)


def default_file(name: str) -> Path:
    """A file shipped with the package (``defaults/``): config template, vocabulary."""
    return Path(str(resources.files("deep_gallery") / "defaults" / name))


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
    """A text file the user may have edited (config, vocabulary): UTF-8, which Deep Gallery
    writes, else the system's encoding (e.g. saved by an old Windows editor).
    Without an explicit encoding Python uses the system's, cp1252 on Windows, which
    garbles or rejects UTF-8 (a folder or tag with ü or 出口)."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return Path(path).read_text(errors="replace")


def find_config(explicit: str | Path | None = None) -> Path:
    """The config file to use: ``--config``, else ``$DEEP_GALLERY_CONFIG``, else
    ``./config.yaml`` if one exists (a project-local setup), else the user's
    config, created from the template on first run."""
    for candidate in (
        explicit,
        os.environ.get("DEEP_GALLERY_CONFIG") or os.environ.get("IMGMIND_CONFIG") or os.environ.get("RIFFLE_CONFIG"),
    ):
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
