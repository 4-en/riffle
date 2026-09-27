import os

import pytest

from riffle import db as _db

from riffle.scan import scan
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
    assert p["focal_length"] == 50 and p["focal_length_35"] == 50
    assert p["aperture"] == 4 and p["iso"] == 400
    assert p["exposure_time"] == pytest.approx(0.004)
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


def test_v1_catalogue_is_migrated_and_metadata_backfilled(cfg, archive_dir):
    """A catalogue from before the exposure columns existed gets them filled in
    on the next scan, without invalidating derived data."""
    import sqlite3

    from riffle import db

    path = cfg.db_path
    path.parent.mkdir(parents=True)
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA)
    old.execute("PRAGMA user_version = 1")
    old.commit()
    old.close()

    conn = db.connect(path)  # migrates to v2
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    scan(conn, cfg)
    # Simulate rows written by the old version: no exposure data, stale meta_version.
    conn.execute("UPDATE photos SET iso = NULL, aperture = NULL, meta_version = 0, phash = 'keep'")
    conn.commit()

    result = scan(conn, cfg)
    assert result.unchanged == 5 and result.changed == 0
    assert result.metadata_refreshed == 4  # the broken file stays an error
    p = photo(conn, "IMG_0001.jpg")
    assert p["iso"] == 400 and p["aperture"] == 4
    assert p["phash"] == "keep"  # derived data untouched
    assert scan(conn, cfg).metadata_refreshed == 0
    conn.close()


@pytest.mark.parametrize("version", range(1, _db.SCHEMA_VERSION))
def test_every_older_catalogue_migrates_to_the_current_schema(tmp_path, version):
    """A catalogue left at any older version ends up with the same tables and
    columns as a new one (guards against a migration without a version bump)."""
    import sqlite3

    from riffle import db

    def columns(conn):
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")] for t in tables}

    old = sqlite3.connect(tmp_path / "old.sqlite3")
    old.executescript(db.SCHEMA)
    for script in db.MIGRATIONS[: version - 1]:
        old.executescript(script)
    old.execute(f"PRAGMA user_version = {version}")
    old.commit()
    old.close()

    migrated = db.connect(tmp_path / "old.sqlite3")
    fresh = db.connect(tmp_path / "new.sqlite3")
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert {t: sorted(c) for t, c in columns(migrated).items()} == {t: sorted(c) for t, c in columns(fresh).items()}
