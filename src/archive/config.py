"""Configuration loading. All paths are resolved relative to the config file."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ModelConfig:
    name: str = "ViT-B-16"
    pretrained: str = "laion2b_s34b_b88k"
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
        }
    )


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


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Copy config.example.yaml to config.yaml."
        )
    raw = yaml.safe_load(path.read_text()) or {}
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
        name=m.get("name", "ViT-B-16"),
        pretrained=m.get("pretrained", "laion2b_s34b_b88k"),
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
        data_dir=resolve(raw.get("data_dir", "data")),
        vocabulary_path=resolve(raw.get("vocabulary", "vocabulary.yaml")),
        model=model,
        tags=tags,
        phash_max_distance=int((raw.get("dupes") or {}).get("phash_max_distance", 8)),
    )


def set_sources(cfg: Config, sources: list[Path]) -> None:
    """Rewrite the ``sources`` list in the config file and update ``cfg``.

    Only the ``sources:`` block is replaced, so comments and formatting elsewhere
    in the file are kept. Entries that are unchanged keep their original spelling
    (e.g. relative paths)."""
    if cfg.path is None:
        raise ValueError("config was not loaded from a file")
    text = cfg.path.read_text()
    raw = yaml.safe_load(text) or {}
    original = {
        (Path(p).expanduser() if Path(p).expanduser().is_absolute() else cfg.root / p).resolve(): p
        for p in raw.get("sources") or []
    }
    entries = [original.get(s, str(s)) for s in sources]
    block = "sources:\n" + "".join(f"  - {json.dumps(str(e))}\n" for e in entries)
    if not entries:
        block = "sources: []\n"

    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if re.match(r"sources\s*:", l)), None)
    if start is None:
        new_text = block + text
    else:
        end = start + 1
        while end < len(lines) and (lines[end][:1] in (" ", "\t", "-") or not lines[end].strip()):
            end += 1
        # Leave blank lines that separate the block from the next key where they were.
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        new_text = "".join(lines[:start]) + block + "".join(lines[end:])

    tmp = cfg.path.with_suffix(".tmp")
    tmp.write_text(new_text)
    tmp.replace(cfg.path)
    cfg.sources = list(sources)
