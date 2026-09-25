import os

import pytest

from archive.scan import scan
from conftest import photo


def test_scan_catalogues_images_and_skips_excludes(cfg, conn):
    result = scan(conn, cfg)
    paths = sorted(r["rel_path"] for r in conn.execute("SELECT rel_path FROM photos"))
    assert paths == [
        "trip/IMG_0001.jpg",
        "trip/IMG_0002.jpg",
        "trip/IMG_0002_edit.png",
        "trip/IMG_0003.png",
        "trip/broken.jpg",
    ]
    assert result.added == 5
    assert sorted(f.rel_path for f in result.raws) == ["trip/RAW/IMG_0001.CR3", "trip/orphan.NEF"]


def test_exif_metadata(cfg, conn):
    scan(conn, cfg)
    p = photo(conn, "IMG_0001.jpg")
    assert p["taken_at"] == "2024:05:01 10:00:00"
    assert p["tz_offset"] == "+08:00"
    assert p["camera"] == "Canon EOS R5"
    assert p["lens"] == "RF24-105mm F4 L IS USM"
    assert p["lat"] == pytest.approx(39.9)
    assert p["lon"] == pytest.approx(116.3833, abs=1e-3)
    # Orientation 6: stored dimensions are as displayed.
    assert (p["width"], p["height"]) == (400, 600)
    assert len(p["sha256"]) == 64


def test_unreadable_file_is_recorded_as_error(cfg, conn):
    result = scan(conn, cfg)
    p = photo(conn, "broken.jpg")
    assert p["status"] == "error"
    assert p["error"]
    assert result.errors == 1


def test_rescan_skips_unchanged(cfg, conn):
    scan(conn, cfg)
    again = scan(conn, cfg)
    assert again.unchanged == 5
    assert again.added == again.changed == again.moved == 0


def test_move_keeps_id_and_missing_is_marked(cfg, conn, archive_dir):
    scan(conn, cfg)
    before = photo(conn, "IMG_0003.png")["id"]
    trip = archive_dir / "photos" / "trip"
    (trip / "moved").mkdir()
    os.rename(trip / "IMG_0003.png", trip / "moved" / "IMG_0003.png")
    (trip / "IMG_0002.jpg").unlink()

    result = scan(conn, cfg)
    assert result.moved == 1
    assert result.missing == 1
    assert photo(conn, "moved/IMG_0003.png")["id"] == before
    assert photo(conn, "IMG_0002.jpg")["status"] == "missing"


def test_changed_file_invalidates_derived_data(cfg, conn, archive_dir):
    from PIL import Image

    scan(conn, cfg)
    p = photo(conn, "IMG_0003.png")
    cfg.thumbs_dir.mkdir(parents=True)
    thumb = cfg.thumbs_dir / f"{p['id']}.jpg"
    thumb.write_bytes(b"x")
    conn.execute("UPDATE photos SET phash = 'abc' WHERE id = ?", (p["id"],))

    path = archive_dir / "photos" / "trip" / "IMG_0003.png"
    Image.new("RGB", (50, 50), "red").save(path)
    os.utime(path, (1, 1))

    result = scan(conn, cfg)
    assert result.changed == 1
    q = photo(conn, "IMG_0003.png")
    assert q["id"] == p["id"]
    assert q["sha256"] != p["sha256"]
    assert q["phash"] is None
    assert not thumb.exists()
