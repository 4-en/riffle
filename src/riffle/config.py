"""Configuration loading. All paths are resolved relative to the config file."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import paths
from .paths import read_text


@dataclass
class ModelConfig:
    name: str = "ViT-L-14-quickgelu"
    pretrained: str = "dfn2b"
    device: str = "auto"
    batch_size: int = 32

    @property
    def model_id(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{self.name}__{self.pretrained}")


@dataclass
class FamilyThresholds:
    min_prob: float
    max_tags: int


@dataclass
class TagConfig:
    softmax_scale: float = 100.0
    families: dict[str, FamilyThresholds] = field(
        default_factory=lambda: {
            "subject": FamilyThresholds(0.15, 3),
            "scene": FamilyThresholds(0.30, 1),
            # confident only: the softmax always picks some kind, and a wrong one
            # would hide a photo when that kind is excluded
            "kind": FamilyThresholds(0.70, 1),
        }
    )


@dataclass
class StackConfig:
    max_gap_seconds: float = 30.0
    min_similarity: float = 0.92  # tuned for the default model; see config.example.yaml


@dataclass
class LocationConfig:
    max_gap_minutes: float = 30.0  # outside visits/routes, use timeline points at most this far in time
    min_population: int = 0  # place names: nearest town/village with at least this many inhabitants


@dataclass
class Config:
    root: Path
    sources: list[Path]
    exclude: list[str]
    image_extensions: set[str]
    raw_extensions: set[str]
    raw_search_dirs: list[str]
    data_dir: Path
    vocabulary_path: Path
    model: ModelConfig
    tags: TagConfig
    phash_max_distance: int = 8
    stacks: StackConfig = field(default_factory=StackConfig)
    # Pick/reject flags: user data, deliberately outside data_dir (which stays disposable).
    selections_path: Path | None = None
    # Phone location history exports (Google Timeline JSON, Records.json, GPX): input, referenced in place.
    location_history: list[Path] = field(default_factory=list)
    location: LocationConfig = field(default_factory=LocationConfig)
    path: Path | None = None  # the config file, when loaded from one

    @property
    def db_path(self) -> Path:
        return self.data_dir / "catalogue.sqlite3"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def previews_dir(self) -> Path:
        return self.data_dir / "previews"

    @property
    def embeddings_dir(self) -> Path:
        return self.data_dir / "embeddings"


DEFAULT_IMAGE_EXT = [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic"]
DEFAULT_RAW_EXT = [".cr2", ".cr3", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2"]


def _exts(values) -> set[str]:
    return {("." + v.lstrip(".")).lower() for v in values}


def _vocabulary(raw: dict, root: Path, resolve) -> Path:
    """``vocabulary`` from the config, else vocabulary.yaml next to it, else the default."""
    if raw.get("vocabulary"):
        return resolve(raw["vocabulary"])
    local = root / "vocabulary.yaml"
    return local if local.exists() else paths.default_file("vocabulary.yaml")


def load_config(path: str | Path | None = None) -> Config:
    """Load settings; without a path, the one ``paths.find_config`` picks."""
    path = paths.find_config(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Run without --config to use (and create) the default one."
        )
    raw = yaml.safe_load(read_text(path)) or {}
    cfg = config_from_dict(raw, path.parent)
    cfg.path = path
    return cfg


def config_from_dict(raw: dict, root: Path) -> Config:
    root = Path(root).resolve()

    def resolve(p: str) -> Path:
        q = Path(p).expanduser()
        return q if q.is_absolute() else (root / q).resolve()

    m = raw.get("model") or {}
    model = ModelConfig(
        name=m.get("name", ModelConfig.name),
        pretrained=m.get("pretrained", ModelConfig.pretrained),
        device=m.get("device", "auto"),
        batch_size=int(m.get("batch_size", 32)),
    )

    t = raw.get("tags") or {}
    tags = TagConfig(softmax_scale=float(t.get("softmax_scale", 100)))
    for family, spec in t.items():
        if isinstance(spec, dict):
            tags.families[family] = FamilyThresholds(
                float(spec.get("min_prob", 0.2)), int(spec.get("max_tags", 1))
            )

    return Config(
        root=root,
        sources=[resolve(s) for s in raw.get("sources", [])],
        exclude=list(raw.get("exclude", [])),
        image_extensions=_exts(raw.get("image_extensions", DEFAULT_IMAGE_EXT)),
        raw_extensions=_exts(raw.get("raw_extensions", DEFAULT_RAW_EXT)),
        raw_search_dirs=list(raw.get("raw_search_dirs", [".", "RAW", "../RAW"])),
        data_dir=resolve(raw["data_dir"]) if raw.get("data_dir") else paths.cache_dir(),
        vocabulary_path=_vocabulary(raw, root, resolve),
        model=model,
        tags=tags,
        phash_max_distance=int((raw.get("dupes") or {}).get("phash_max_distance", 8)),
        stacks=StackConfig(
            max_gap_seconds=float((raw.get("stacks") or {}).get("max_gap_seconds", 30)),
            min_similarity=float((raw.get("stacks") or {}).get("min_similarity", StackConfig.min_similarity)),
        ),
        selections_path=resolve(raw["selections"]) if raw.get("selections") else paths.data_dir() / "selections.sqlite3",
        location_history=[resolve(p) for p in raw.get("location_history") or []],
        location=LocationConfig(
            max_gap_minutes=float((raw.get("location") or {}).get("max_gap_minutes", 30)),
            min_population=int((raw.get("location") or {}).get("min_population", 0)),
        ),
    )


def _write_path_list(cfg: Config, key: str, paths: list[Path]) -> None:
    """Rewrite the top-level ``key:`` list of paths in the config file.

    Only that block is replaced, so comments and formatting elsewhere in the file
    are kept. Entries that are unchanged keep their original spelling (e.g.
    relative paths). A missing key is added at the end."""
    if cfg.path is None:
        raise ValueError("config was not loaded from a file")
    text = read_text(cfg.path)
    raw = yaml.safe_load(text) or {}
    original = {
        (Path(p).expanduser() if Path(p).expanduser().is_absolute() else cfg.root / p).resolve(): p
        for p in raw.get(key) or []
    }
    entries = [original.get(p, str(p)) for p in paths]
    block = f"{key}:\n" + "".join(f"  - {json.dumps(str(e))}\n" for e in entries)
    if not entries:
        block = f"{key}: []\n"

    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if re.match(rf"{re.escape(key)}\s*:", l)), None)
    if start is None:
        new_text = text + ("" if text.endswith("\n") or not text else "\n") + "\n" + block
    else:
        end = start + 1
        while end < len(lines) and (lines[end][:1] in (" ", "\t", "-") or not lines[end].strip()):
            end += 1
        # Leave blank lines that separate the block from the next key where they were.
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        new_text = "".join(lines[:start]) + block + "".join(lines[end:])

    tmp = cfg.path.with_suffix(".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(cfg.path)


def set_sources(cfg: Config, sources: list[Path]) -> None:
    """Rewrite the ``sources`` list in the config file and update ``cfg``."""
    _write_path_list(cfg, "sources", sources)
    cfg.sources = list(sources)


def set_location_history(cfg: Config, files: list[Path]) -> None:
    """Rewrite the ``location_history`` list in the config file and update ``cfg``."""
    _write_path_list(cfg, "location_history", files)
    cfg.location_history = list(files)
