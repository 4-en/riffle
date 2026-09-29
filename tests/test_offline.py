"""Folders that cannot be reached (an unplugged drive) are offline, not deleted: their
photos stay as they were until the folder is back, or until Settings forgets it."""

import time

import pytest
from fastapi.testclient import TestClient

from riffle import db
from riffle.embed import load_embeddings
from riffle.scan import scan
from riffle.server import create_app
from conftest import FakeClip, fake_index

ALL = {"dupes": "all"}


def wait_index(c, timeout=30):
    deadline = time.time() + timeout
    while c.get("/api/index").json()["running"]:
        assert time.time() < deadline, "index did not finish"
        time.sleep(0.05)
    status = c.get("/api/index").json()
    assert status["error"] is None, status
    return status


def index(c):
    c.post("/api/index", json={})
    return wait_index(c)


def listed(c):
    return sorted(i["id"] for i in c.get("/api/photos", params=ALL).json()["items"])


@pytest.fixture
def client(indexed):
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text, index_runner=fake_index)) as c:
        yield c


def test_an_unplugged_folder_keeps_its_photos(client, indexed, archive_dir):
    c = client
    before = listed(c)
    _, embedded = load_embeddings(indexed)
    photos = archive_dir / "photos"
    photos.rename(archive_dir / "elsewhere")  # the drive is unplugged
    index(c)
    assert listed(c) == before  # still there, with everything
    _, still = load_embeddings(indexed)
    assert set(embedded.tolist()) <= set(still.tolist())
    folder = c.get("/api/sources").json()["sources"][0]
    assert folder["offline"] is True and folder["photos"] == len(before)
    assert c.get(f"/api/photos/{before[0]}").json()["offline"] is True

    (archive_dir / "elsewhere").rename(photos)  # plugged in again
    index(c)
    assert listed(c) == before
    assert c.get("/api/sources").json()["sources"][0]["offline"] is False
    assert c.get(f"/api/photos/{before[0]}").json()["offline"] is False


def test_an_empty_mount_point_is_offline_too(client, archive_dir):
    c = client
    before = listed(c)
    photos = archive_dir / "photos"
    photos.rename(archive_dir / "elsewhere")
    photos.mkdir()  # the mount point without the drive
    index(c)
    assert listed(c) == before
    assert c.get("/api/sources").json()["sources"][0]["offline"] is True


def test_a_deleted_photo_in_a_reachable_folder_is_gone(client, archive_dir, conn):
    c = client
    before = listed(c)
    (archive_dir / "photos" / "trip" / "IMG_0003.png").unlink()
    index(c)
    assert len(listed(c)) == len(before) - 1  # the folder is online: a missing file is really gone


def own_profile(c):
    """A profile with the default's folders (the fixture's config is not a file, so the
    default profile's folders cannot be changed)."""
    slug = c.post("/api/profiles", json={"name": "Mine", "copy_from": "default", "parts": ["folders"]}).json()["slug"]
    c.post(f"/api/profiles/{slug}/activate", json={})


def test_forgetting_an_offline_folder(client, indexed, archive_dir, conn):
    c = client
    own_profile(c)
    before = listed(c)
    photos = archive_dir / "photos"
    photos.rename(archive_dir / "elsewhere")
    index(c)
    res = c.request("DELETE", "/api/sources", json={"path": str(photos), "forget": True}).json()
    assert res["forgotten"] == len(before) + 1  # (and the unreadable one)
    wait_index(c)
    assert listed(c) == [] and c.get("/api/sources").json()["sources"] == []
    _, embedded = load_embeddings(indexed)
    assert not set(before) & set(embedded.tolist())
    assert c.get("/api/sources").json()["missing"] >= len(before)  # Clean up removes the rest


def test_a_moved_folder_keeps_its_photos(client, indexed, archive_dir, conn):
    c = client
    own_profile(c)
    before = listed(c)
    photos = archive_dir / "photos"
    moved = archive_dir / "moved"
    photos.rename(moved)
    c.post("/api/sources", json={"path": str(moved)})  # added from its new place
    wait_index(c)
    assert listed(c) == before  # the same photos (by content), not copies
    assert c.post("/api/sources", json={"path": str(moved)}).status_code == 409
    old = next(s for s in c.get("/api/sources").json()["sources"] if s["path"] == str(photos))
    assert old["photos"] == 0 and old["offline"] is False  # nothing left there: just remove it


def test_export_reports_unreachable_originals(client, archive_dir, tmp_path):
    c = client
    ids = listed(c)
    (archive_dir / "photos").rename(archive_dir / "elsewhere")
    out = tmp_path / "out"
    out.mkdir()
    assert c.post("/api/export", json={"folder": str(out), "photo_ids": ids[:2], "add_location": False}).status_code == 200
    deadline = time.time() + 10
    while c.get("/api/export").json()["running"]:
        assert time.time() < deadline
        time.sleep(0.05)
    result = c.get("/api/export").json()["result"]
    assert result["unreachable"] == 2 and result["copied"] == 0


def test_scan_reports_offline_folders(indexed, archive_dir):
    photos = archive_dir / "photos"
    photos.rename(archive_dir / "elsewhere")
    conn = db.connect(indexed.db_path)
    try:
        result = scan(conn, indexed)
        assert result.offline == [(photos, 4)] and result.missing == 0
        assert conn.execute("SELECT COUNT(*) FROM photos WHERE status = 'ok'").fetchone()[0] == 4
    finally:
        conn.close()
