"""Per-profile photo folders: each profile sees only its own folders (library.py),
indexing covers every profile's (profiles.indexed_roots), and folders no profile
shows keep their data until a clean-up."""

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from riffle import profiles, selections
from riffle.embed import load_embeddings
from riffle.server import create_app
from conftest import FakeClip, _pattern, fake_index

ALL = {"dupes": "all"}


def wait_index(c, timeout=30):
    deadline = time.time() + timeout
    while c.get("/api/index").json()["running"]:
        assert time.time() < deadline, "index did not finish"
        time.sleep(0.05)
    status = c.get("/api/index").json()
    assert status["error"] is None, status
    return status


def wait_export(c, timeout=10):
    deadline = time.time() + timeout
    while c.get("/api/export").json()["running"]:
        assert time.time() < deadline, "export did not finish"
        time.sleep(0.05)


def listed(c, **params):
    return sorted(i["id"] for i in c.get("/api/photos", params={**ALL, **params}).json()["items"])


def ids_in(conn, folder):
    return sorted(r[0] for r in conn.execute(
        "SELECT id FROM photos WHERE status = 'ok' AND (source || '/' || rel_path) LIKE ?", (f"{folder}/%",)
    ))


@pytest.fixture
def other_folder(archive_dir):
    """A second photo folder beside the fixture's, with two photos of its own."""
    other = archive_dir / "other"
    other.mkdir()
    _pattern(7).save(other / "A.jpg", quality=95)
    _pattern(8).save(other / "B.jpg", quality=95)
    return other


@pytest.fixture
def client(indexed, other_folder):
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text, index_runner=fake_index)) as c:
        yield c


def switch_to_new(c, name, **body):
    slug = c.post("/api/profiles", json={"name": name, **body}).json()["slug"]
    assert c.post(f"/api/profiles/{slug}/activate", json={}).status_code == 200
    return slug


def test_each_profile_sees_only_its_folders(client, indexed, conn, other_folder, tmp_path):
    c = client
    mine = ids_in(conn, indexed.sources[0])
    assert listed(c) == mine and len(mine) == 4

    switch_to_new(c, "Other")
    assert listed(c) == [] and c.get("/api/tags").json()["photos"] == 0  # a new profile: no folders
    res = c.post("/api/sources", json={"path": str(other_folder)}).json()
    assert res["indexing"] is True  # not indexed by anyone yet
    wait_index(c)
    theirs = ids_in(conn, other_folder)
    assert len(theirs) == 2 and not set(theirs) & set(mine)

    # Everything in this profile is its folder's two photos, nothing of the default's.
    assert listed(c) == theirs
    assert c.get("/api/tags").json()["photos"] == 2
    assert sorted(c.get("/api/ids", params=ALL).json()["ids"]) == theirs
    folders = c.get("/api/facets", params=ALL).json()["folder"]
    assert folders and all(str(other_folder) in f["value"] for f in folders)
    assert sorted(i["id"] for i in c.get("/api/search/text", params={"q": "red", **ALL}).json()["items"]) == theirs
    assert {i["id"] for i in c.get(f"/api/search/similar/{theirs[0]}", params=ALL).json()["items"]} <= set(theirs)
    groups = c.get("/api/groups", params={"group": "similar", **ALL}).json()
    assert sum(g["count"] for g in groups["groups"]) == 2
    assert {p[0] for p in c.get("/api/similar/map", params=ALL).json()["points"]} <= set(theirs)
    assert c.get("/api/discover/start").json()["id"] in theirs
    step = c.get(f"/api/discover/{theirs[0]}").json()
    assert {p["id"] for b in step["branches"] for p in b["photos"]} <= set(theirs)
    draft = c.post("/api/curate", params=ALL, json={"n": 6, "include_rejects": True}).json()
    assert {i["id"] for i in draft["items"]} <= set(theirs) and draft["candidates"] <= 2
    # A search elsewhere (the default's photos) is not a way in either.
    assert c.get(f"/api/search/similar/{mine[0]}", params=ALL).status_code in (200, 404)
    assert not {i["id"] for i in c.get(f"/api/search/similar/{mine[0]}", params=ALL).json().get("items", [])} & set(mine)

    # Picks of this profile, and "export all picks" copies only its own.
    c.post("/api/flags", json={"ops": [{"ids": [theirs[0]], "flag": "pick"}]})
    out = tmp_path / "out"
    out.mkdir()
    assert c.post("/api/export", json={"folder": str(out), "scope": "all", "add_location": False}).status_code == 200
    wait_export(c)
    assert sorted(p.name for p in out.rglob("*.jpg")) == ["A.jpg"]

    # Back in the default profile: its own photos, not the other folder's.
    c.post("/api/profiles/default/activate", json={})
    assert listed(c) == mine and c.get("/api/tags").json()["photos"] == 4
    sources = c.get("/api/sources").json()
    assert [s["path"] for s in sources["sources"]] == [str(indexed.sources[0])]
    assert [(o["path"], o["photos"], o["profiles"]) for o in sources["others"]] == [(str(other_folder), 2, ["Other"])]


def test_a_folder_inside_another_profiles_folder(client, indexed, conn, archive_dir):
    c = client
    _pattern(9).save(archive_dir / "photos" / "top.jpg", quality=95)  # beside trip/, not in it
    c.post("/api/index", json={})
    wait_index(c)
    assert len(listed(c)) == 5
    trip = archive_dir / "photos" / "trip"
    switch_to_new(c, "Trip")
    assert c.post("/api/sources", json={"path": str(trip)}).json()["indexing"] is False  # inside an indexed folder
    assert listed(c) == ids_in(conn, trip) and len(listed(c)) == 4  # top.jpg is not in trip/
    assert c.get("/api/tags").json()["photos"] == 4
    browse = c.get("/api/fs", params={"path": str(archive_dir / "photos")}).json()
    assert browse["source"] is None and browse["indexed"] == str(archive_dir / "photos")
    assert profiles.indexed_roots(indexed) == [archive_dir / "photos"]  # one root: each file catalogued once


def test_folders_no_profile_shows_keep_their_data(client, indexed, conn, other_folder):
    c = client
    switch_to_new(c, "Other")
    c.post("/api/sources", json={"path": str(other_folder)})
    wait_index(c)
    theirs = ids_in(conn, other_folder)
    _, before = load_embeddings(indexed)
    assert set(theirs) <= set(before.tolist())

    # Removed from the only profile that had it: hidden, but its thumbnails and embeddings stay.
    c.request("DELETE", "/api/sources", json={"path": str(other_folder)})
    wait_index(c)
    assert listed(c) == []
    status = dict(conn.execute("SELECT id, status FROM photos WHERE id IN (?, ?)", theirs).fetchall())
    assert set(status.values()) == {"hidden"}
    _, kept = load_embeddings(indexed)
    assert set(theirs) <= set(kept.tolist())
    assert all((indexed.thumbs_dir / f"{i}.jpg").exists() for i in theirs)
    assert c.get("/api/sources").json()["missing"] == 2

    # Added back: the same photos (ids, embeddings), nothing embedded again.
    E0, ids0 = load_embeddings(indexed)
    c.post("/api/sources", json={"path": str(other_folder)})
    wait_index(c)
    assert listed(c) == theirs
    E1, ids1 = load_embeddings(indexed)
    assert np.array_equal(ids0, ids1) and np.allclose(E0, E1)

    # Removed again, then cleaned up: gone for good.
    c.request("DELETE", "/api/sources", json={"path": str(other_folder)})
    wait_index(c)
    assert c.post("/api/cleanup", json={}).json()["removed"] == 2
    wait_index(c)
    assert conn.execute("SELECT COUNT(*) FROM photos WHERE id IN (?, ?)", theirs).fetchone()[0] == 0
    _, after = load_embeddings(indexed)
    assert not set(theirs) & set(after.tolist())
    assert not any((indexed.thumbs_dir / f"{i}.jpg").exists() for i in theirs)


def test_profiles_and_their_folders(indexed, other_folder):
    # A profile from before per-profile folders shows the config's folders.
    old = profiles.create(indexed, "Old")
    conn = selections.connect(profiles.path_for(indexed, old))
    with conn:
        conn.execute("DELETE FROM folders")
        conn.execute("DELETE FROM profile_settings")
    conn.close()
    assert profiles.folders(indexed, old) == list(indexed.sources)
    # New: empty; copied: the folders along, unless left out.
    assert profiles.folders(indexed, profiles.create(indexed, "Empty")) == []
    profiles.set_folders(indexed, old, [*indexed.sources, other_folder])
    assert profiles.folders(indexed, profiles.create(indexed, "Copy", copy_from=old)) == [*indexed.sources, other_folder]
    assert profiles.folders(indexed, profiles.create(indexed, "Flags", copy_from=old, parts={"flags"})) == []
    # Indexing covers every profile's folders.
    assert profiles.indexed_roots(indexed) == sorted([*indexed.sources, other_folder], key=str)
    # Adding a folder: inside one already there is refused, around ones replaces them.
    with pytest.raises(profiles.ProfileError):
        profiles.with_folder([other_folder.parent], other_folder)
    assert profiles.with_folder([other_folder], other_folder.parent) == [other_folder.parent]
