"""Profiles: switchable selections databases (profiles.py and the API)."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from riffle import profiles, selections
from riffle.server import create_app
from conftest import FakeClip, photo


@pytest.fixture
def app(indexed):
    return create_app(indexed, text_encoder=FakeClip().encode_text)


def ids(conn, *names):
    return [photo(conn, n)["id"] for n in names]


def flags(c, **params):
    return c.get("/api/facets", params={"dupes": "all", **params}).json()["flag"]


def test_without_profiles_nothing_changes(indexed, app):
    with TestClient(app) as c:
        listed = c.get("/api/profiles").json()
        assert listed["active"] == "default"
        assert [(p["slug"], p["name"], p["path"]) for p in listed["profiles"]] == [("default", "Default", str(indexed.selections_path))]
        assert c.get("/api/health").json()["profile"] == "default"
    assert profiles.active_path(indexed) == indexed.selections_path


def test_switching_swaps_flags_tags_and_exports(indexed, app, conn):
    a, b = ids(conn, "IMG_0001.jpg", "IMG_0003.png")
    with TestClient(app) as c:
        c.post("/api/flags", json={"ops": [{"ids": [a], "flag": "pick"}]})
        c.post("/api/custom-tags", json={"name": "Mine", "photo_ids": [a]})
        slug = c.post("/api/profiles", json={"name": "Photo book"}).json()["slug"]
        assert slug == "photo-book"
        assert c.post(f"/api/profiles/{slug}/activate", json={}).json()["active"] == slug
        assert c.get("/api/tags").json()["photos"] == 0  # an empty profile: no folders yet
        added = c.post("/api/sources", json={"path": str(indexed.sources[0])}).json()
        assert added["indexing"] is False  # indexed already (the default profile has it): instant
        assert flags(c)["pick"] == 0 and c.get("/api/tags").json()["custom"] == []  # empty profile
        c.post("/api/flags", json={"ops": [{"ids": [b], "flag": "reject"}]})
        assert flags(c)["reject"] == 1
        assert c.get("/api/health").json()["profile_version"] == 1

        c.post("/api/profiles/default/activate", json={})
        back = flags(c)
        assert back["pick"] == 1 and back["reject"] == 0  # the default profile is untouched
        assert [t["name"] for t in c.get("/api/tags").json()["custom"]] == ["Mine"]
        counts = {p["slug"]: (p["picks"], p["rejects"], p["tags"]) for p in c.get("/api/profiles").json()["profiles"]}
        assert counts == {"default": (1, 0, 1), "photo-book": (0, 1, 0)}


def test_copying_chosen_parts(indexed, app, conn):
    a, far = ids(conn, "IMG_0001.jpg", "IMG_0003.png")
    with TestClient(app) as c:
        c.post("/api/flags", json={"ops": [{"ids": [a], "flag": "pick"}]})
        tag = c.post("/api/custom-tags", json={"name": "Mine", "photo_ids": [a]}).json()["id"]
        # (stored directly: the fake embeddings make every photo a near-copy, which the API refuses)
        selections.update_tag(conn, profiles.path_for(indexed, "default"), tag, add_negatives=[far])
        slug = c.post("/api/profiles", json={"name": "Tags only", "copy_from": "default", "parts": ["tags", "folders"]}).json()["slug"]
        c.post(f"/api/profiles/{slug}/activate", json={})
        assert flags(c)["pick"] == 0  # flags not copied
        custom = c.get("/api/tags").json()["custom"]
        assert [(t["id"], t["name"], t["examples"], t["negatives"]) for t in custom] == [(tag, "Mine", [a], [far])]
        assert a in [i["id"] for i in c.get("/api/photos", params={"ctags": tag, "dupes": "all"}).json()["items"]]
        everything = c.post("/api/profiles", json={"name": "All", "copy_from": "default"}).json()["slug"]
        assert [p for p in c.get("/api/profiles").json()["profiles"] if p["slug"] == everything][0]["picks"] == 1
        assert c.post("/api/profiles", json={"name": "Bad", "parts": ["nonsense"], "copy_from": "default"}).status_code == 400
        assert c.post("/api/profiles", json={"name": "tags ONLY"}).status_code == 409  # names ignore case
        assert c.post("/api/profiles", json={"name": " "}).status_code == 400


def test_rename_delete_and_persistence(indexed, conn):
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        slug = c.post("/api/profiles", json={"name": "Trip"}).json()["slug"]
        other = c.post("/api/profiles", json={"name": "Other"}).json()["slug"]
        c.post(f"/api/profiles/{slug}", json={"name": "Trip 2026"})
        assert "Trip 2026" in [p["name"] for p in c.get("/api/profiles").json()["profiles"]]
        c.post(f"/api/profiles/{slug}/activate", json={})
        assert c.delete("/api/profiles/default").status_code == 400
        assert c.delete(f"/api/profiles/{slug}").status_code == 400  # the active one
        moved = c.delete(f"/api/profiles/{other}").json()["moved_to"]
        assert "deleted" in moved and not profiles.path_for(indexed, other).exists()
        assert c.post("/api/profiles/nope/activate", json={}).status_code == 404
        assert c.post("/api/profiles/../x/activate", json={}).status_code in (404, 405)
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:  # a restart
        assert c.get("/api/profiles").json()["active"] == slug


def test_taste_model_per_profile(indexed, app):
    with TestClient(app) as c:
        slug = c.post("/api/profiles", json={"name": "B"}).json()["slug"]
        c.post(f"/api/profiles/{slug}/activate", json={})
        c.post("/api/taste/calibrate", json={})
        assert (indexed.embeddings_dir / f"{indexed.model.model_id}.{slug}.taste.npz").exists()
        assert not (indexed.embeddings_dir / f"{indexed.model.model_id}.taste.npz").exists()  # the default's file untouched


def test_an_export_records_in_its_own_profile(indexed, conn, tmp_path):
    a = ids(conn, "IMG_0001.jpg")[0]
    release = threading.Event()
    real_mark = selections.mark_exported

    def slow_mark(*args, **kwargs):
        release.wait(10)
        return real_mark(*args, **kwargs)

    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        slug = c.post("/api/profiles", json={"name": "B"}).json()["slug"]
        selections.mark_exported = slow_mark
        try:
            c.post("/api/export", json={"folder": str(tmp_path), "name": "out", "photo_ids": [a]})
            assert c.post(f"/api/profiles/{slug}/activate", json={}).status_code == 409  # not during an export
            release.set()
            for _ in range(200):
                if not c.get("/api/export").json()["running"]:
                    break
                time.sleep(0.05)
        finally:
            selections.mark_exported = real_mark
        c.post(f"/api/profiles/{slug}/activate", json={})
        assert c.get("/api/facets").json()["exported"]["yes"] == 0  # recorded in the default profile
        c.post("/api/profiles/default/activate", json={})
        assert c.get("/api/facets").json()["exported"]["yes"] == 1
