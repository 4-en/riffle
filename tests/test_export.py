"""Exporting picks: content modes, naming, safety."""

import csv
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from archive import selections
from archive.export import ExportError, run_export
from archive.scan import sha256_file
from archive.server import create_app
from conftest import FakeClip, fake_index, photo


@pytest.fixture
def picked(indexed, conn):
    """IMG_0001 (has a RAW) and IMG_0003 (no RAW) are picked."""
    ids = [photo(conn, "IMG_0001.jpg")["id"], photo(conn, "IMG_0003.png")["id"]]
    selections.set_flags(conn, indexed.selections_path, [(ids, "pick")])
    return indexed, ids


def export(cfg, ids, dest, **kw):
    return run_export(cfg, report=lambda _: None, photo_ids=ids, folder=str(dest), **kw)


def files_in(folder: Path):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


@pytest.mark.parametrize(
    "content, fallback, expected, without_raw",
    [
        ("images", True, ["IMG_0001.jpg", "IMG_0003.png"], 0),
        ("images_raws", True, ["IMG_0001.CR3", "IMG_0001.jpg", "IMG_0003.png"], 1),
        ("raws", True, ["IMG_0001.CR3", "IMG_0003.png"], 1),  # falls back to the image
        ("raws", False, ["IMG_0001.CR3"], 1),
    ],
)
def test_content_modes(picked, tmp_path, content, fallback, expected, without_raw):
    cfg, ids = picked
    dest = tmp_path / "out"
    summary = export(cfg, ids, dest, content=content, raw_fallback=fallback)
    assert files_in(dest) == expected + ["export-manifest.csv"]
    assert summary["copied"] == len(expected) and summary["without_raw"] == without_raw


def test_originals_untouched_and_copies_identical(picked, archive_dir, tmp_path):
    cfg, ids = picked
    src = archive_dir / "photos" / "trip" / "IMG_0001.jpg"
    before = (sha256_file(src), src.stat().st_mtime)
    export(cfg, ids, tmp_path / "out", content="images_raws")
    assert (sha256_file(src), src.stat().st_mtime) == before
    copy = tmp_path / "out" / "IMG_0001.jpg"
    assert sha256_file(copy) == before[0]
    assert copy.stat().st_mtime == pytest.approx(before[1], abs=1)  # copy2 keeps the date


def test_never_overwrites_and_resumes(picked, tmp_path):
    cfg, ids = picked
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "IMG_0003.png").write_bytes(b"someone else's file")

    first = export(cfg, ids, dest)
    assert (dest / "IMG_0003.png").read_bytes() == b"someone else's file"
    assert {"IMG_0001.jpg", "IMG_0003-1.png"} <= set(files_in(dest))
    assert first["copied"] == 2

    again = export(cfg, ids, dest)  # identical files already there are skipped
    assert again == again | {"copied": 0, "skipped": 2}
    assert "export-manifest-1.csv" in files_in(dest)
    rows = list(csv.DictReader(open(dest / "export-manifest-1.csv")))
    assert {r["status"] for r in rows} == {"skipped"}


def test_name_clashes_keep_image_and_raw_together(indexed, conn, archive_dir, tmp_path):
    other = archive_dir / "photos" / "other"
    other.mkdir()
    Image.new("RGB", (64, 64), "blue").save(other / "IMG_0001.jpg")
    fake_index(indexed)
    ids = [photo(conn, "trip/IMG_0001.jpg")["id"], photo(conn, "other/IMG_0001.jpg")["id"]]

    export(indexed, ids, tmp_path / "flat", content="images_raws")
    assert files_in(tmp_path / "flat") == ["IMG_0001-1.jpg", "IMG_0001.CR3", "IMG_0001.jpg", "export-manifest.csv"]

    export(indexed, ids, tmp_path / "tree", content="images_raws", structure="folders")
    assert files_in(tmp_path / "tree") == [
        "export-manifest.csv",
        "photos/other/IMG_0001.jpg",
        "photos/trip/IMG_0001.jpg",
        "photos/trip/RAW/IMG_0001.CR3",
    ]


def test_refuses_destinations_that_would_be_indexed(picked, archive_dir):
    cfg, ids = picked
    with pytest.raises(ExportError, match="inside the photo folder"):
        export(cfg, ids, archive_dir / "photos" / "picks")
    with pytest.raises(ExportError, match="data folder"):
        export(cfg, ids, cfg.data_dir / "export")


def test_export_api(picked, tmp_path):
    cfg, ids = picked
    with TestClient(create_app(cfg, text_encoder=FakeClip().encode_text)) as c:
        body = {"folder": str(tmp_path), "name": "Selection", "content": "images"}
        assert c.post("/api/export", json=body).status_code == 200
        deadline = time.time() + 20
        while (status := c.get("/api/export").json())["running"] and time.time() < deadline:
            time.sleep(0.05)
        assert status["error"] is None
        assert status["result"]["copied"] == 2
        assert files_in(tmp_path / "Selection") == ["IMG_0001.jpg", "IMG_0003.png", "export-manifest.csv"]

        # Only the picks within the filters: IMG_0001 is the only dated pick.
        scoped = c.post(
            "/api/export",
            params={"date_from": "2024-05-01"},
            json=body | {"name": "Dated", "scope": "filtered"},
        )
        assert scoped.status_code == 200
        while c.get("/api/export").json()["running"]:
            time.sleep(0.05)
        assert files_in(tmp_path / "Dated") == ["IMG_0001.jpg", "export-manifest.csv"]

        assert c.post("/api/export", json=body | {"content": "everything"}).status_code == 400
        assert c.post("/api/export", json=body | {"name": "../escape"}).status_code == 400
        assert c.post("/api/export", json=body | {"folder": "relative"}).status_code == 400
        inside = c.post("/api/export", json=body | {"folder": str(cfg.sources[0])})
        assert inside.status_code == 400 and "inside the photo folder" in inside.json()["detail"]
        none = c.post("/api/export", params={"flag": "reject"}, json=body | {"scope": "filtered"})
        assert none.status_code == 400
