"""Where things live, following each platform's conventions (via platformdirs).

On Linux, honouring the XDG variables:

- settings (config.yaml, vocabulary.yaml): ``~/.config/riffle``
- user data (selections.sqlite3, the pick/reject flags): ``~/.local/share/riffle``
- derived data (catalogue, thumbnails, previews, embeddings): ``~/.cache/riffle``,
  safe to delete and rebuilt by indexing

macOS uses ``~/Library/Application Support/riffle`` and ``~/Library/Caches/riffle``;
Windows ``%APPDATA%\\riffle`` (settings and flags, which roam with the user
profile) and ``%LOCALAPPDATA%\\riffle\\Cache``.
"""

from __future__ import annotations

import os
import shutil
import sys
from importlib import resources
from pathlib import Path

import platformdirs

APP = "riffle"


# appauthor=False: no extra "author" folder on Windows (it would be riffle\riffle).
def config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP, appauthor=False, roaming=True))


def data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP, appauthor=False, roaming=True))


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir(APP, appauthor=False))


def default_file(name: str) -> Path:
    """A file shipped with the package (``defaults/``): config template, vocabulary."""
    return Path(str(resources.files("riffle") / "defaults" / name))


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


def find_config(explicit: str | Path | None = None) -> Path:
    """The config file to use: ``--config``, else ``$RIFFLE_CONFIG``, else
    ``./config.yaml`` if one exists (a project-local setup), else the user's
    config, created from the template on first run."""
    for candidate in (explicit, os.environ.get("RIFFLE_CONFIG")):
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
