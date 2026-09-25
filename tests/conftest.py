from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from archive import db
from archive.config import config_from_dict

VOCAB = """
templates: ["a photo of {}"]
subject:
  - red things: [something red, a red object]
  - blue things
  - green things: [something green]
scene: [indoors, outdoors, other]
"""


def _pattern(seed: int, size=(640, 480)) -> Image.Image:
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)
    return Image.fromarray(small).resize(size, Image.Resampling.BICUBIC)


def _exif_jpeg(path: Path) -> None:
    im = _pattern(1, (600, 400))
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "Canon EOS R5"
    exif[0x0112] = 6  # rotate 90° CW on display
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = "2024:05:01 10:00:00"
    sub[0x9011] = "+08:00"
    sub[0xA434] = "RF24-105mm F4 L IS USM"
    sub[0x920A] = 50.0  # focal length
    sub[0xA405] = 50  # 35 mm equivalent
    sub[0x829D] = 4.0  # f-number
    sub[0x829A] = 0.004  # 1/250 s
    sub[0x8827] = 400  # ISO
    gps = exif.get_ifd(0x8825)
    gps.update({1: "N", 2: (39.0, 54.0, 0.0), 3: "E", 4: (116.0, 23.0, 0.0)})
    im.save(path, exif=exif, quality=92)


@pytest.fixture
def archive_dir(tmp_path: Path) -> Path:
    src = tmp_path / "photos"
    trip = src / "trip"
    (trip / "RAW").mkdir(parents=True)
    (src / ".Trashes").mkdir()

    _exif_jpeg(trip / "IMG_0001.jpg")
    (trip / "RAW" / "IMG_0001.CR3").write_bytes(b"not really a raw")
    _pattern(2).save(trip / "IMG_0002.jpg", quality=95)
    _pattern(2).resize((1280, 960)).save(trip / "IMG_0002_edit.png")  # near-duplicate
    _pattern(3).save(trip / "IMG_0003.png")
    (trip / "orphan.NEF").write_bytes(b"raw without a jpeg")
    (trip / "broken.jpg").write_bytes(b"this is not a jpeg")
    _pattern(4).save(src / ".Trashes" / "deleted.jpg")
    return tmp_path


@pytest.fixture
def cfg(archive_dir: Path):
    (archive_dir / "vocabulary.yaml").write_text(VOCAB)
    return config_from_dict(
        {
            "sources": ["photos"],
            "exclude": ["**/.Trashes/**"],
            "model": {"name": "fake", "pretrained": "test", "batch_size": 2},
            "tags": {
                "softmax_scale": 10,
                "subject": {"min_prob": 0.0, "max_tags": 2},
                "scene": {"min_prob": 0.0, "max_tags": 1},
            },
        },
        archive_dir,
    )


@pytest.fixture
def conn(cfg):
    c = db.connect(cfg.db_path)
    yield c
    c.close()


class FakeClip:
    """Deterministic stand-in for CLIP: images map to their mean colour, text to a hashed vector."""

    DIM = 8

    def encode_images(self, images) -> np.ndarray:
        out = []
        for im in images:
            rgb = np.asarray(im.convert("RGB").resize((2, 2)), dtype=np.float32).reshape(-1) / 255
            out.append(np.concatenate([rgb[:6], [1.0, 0.5]]))
        return _norm(np.array(out, dtype=np.float32))

    def encode_text(self, texts: list[str]) -> np.ndarray:
        vecs = []
        for t in texts:
            seed = int(hashlib.md5(t.encode()).hexdigest()[:8], 16)
            vecs.append(np.random.default_rng(seed).normal(size=self.DIM))
        return _norm(np.array(vecs, dtype=np.float32))


def _norm(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=1, keepdims=True)


@pytest.fixture
def fake_clip():
    return FakeClip()


def fake_index(cfg, progress=None, report=print):
    """The full pipeline with the fake encoder (same signature as run_index)."""
    from archive.dupes import compute_phashes, group_duplicates
    from archive.embed import embed_photos, load_embeddings
    from archive.raws import match_raws
    from archive.scan import scan
    from archive.tags import tag_photos
    from archive.thumbs import build_thumbnails

    clip = FakeClip()
    conn = db.connect(cfg.db_path)
    try:
        result = scan(conn, cfg, progress=progress)
        match_raws(conn, cfg, result.raws)
        build_thumbnails(conn, cfg, workers=2, progress=progress)
        embed_photos(conn, cfg, clip=clip, progress=progress)
        E, ids = load_embeddings(cfg)
        tag_photos(conn, cfg, E, ids, clip.encode_text)
        compute_phashes(conn, cfg, progress=progress)
        group_duplicates(conn, cfg)
        report(f"indexed {len(result.images)} images")
    finally:
        conn.close()


@pytest.fixture
def indexed(cfg, conn):
    fake_index(cfg)
    return cfg


def photo(conn, name: str):
    return conn.execute("SELECT * FROM photos WHERE rel_path LIKE ?", (f"%{name}",)).fetchone()
