from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from riffle.server import create_app
from conftest import photo


@pytest.fixture
def client(indexed, fake_clip):
    app = create_app(indexed, text_encoder=fake_clip.encode_text)
    with TestClient(app) as c:
        yield c


def test_list_photos_collapses_duplicates(client, conn):
    all_ = client.get("/api/photos", params={"dupes": "all"}).json()
    assert all_["total"] == 4
    collapsed = client.get("/api/photos").json()
    assert collapsed["total"] == 3
    rep = next(i for i in collapsed["items"] if i["dupe_count"])
    assert rep["dupe_count"] == 2
    first = collapsed["items"][0]
    assert first["taken_at"] == "2024:05:01 10:00:00"  # dated photos sort first
    assert first["has_raw"] is True
    assert client.get(first["thumb"]).status_code == 200


def test_paging(client):
    page = client.get("/api/photos", params={"dupes": "all", "limit": 3, "offset": 3}).json()
    assert page["total"] == 4 and len(page["items"]) == 1


def test_tags_and_tag_filter(client, conn):
    tags = client.get("/api/tags").json()
    assert set(tags["families"]) == {"subject", "scene"}
    assert tags["photos"] == 4 and tags["unmatched_raws"] == 1
    scene = max(tags["families"]["scene"], key=lambda t: t["count"])
    filtered = client.get("/api/photos", params={"tags": str(scene["id"]), "dupes": "all"}).json()
    assert filtered["total"] == scene["count"]
    all_scene = [str(i) for (i,) in conn.execute("SELECT id FROM tags WHERE family = 'scene'")]
    none = client.get("/api/photos", params={"tags": ",".join(all_scene)}).json()
    assert none["total"] == 0  # scene has max_tags 1, so no photo has every scene tag


def test_tag_counts_follow_filter_and_hide_empty_tags(client, conn):
    everything = client.get("/api/tags").json()["families"]
    assert all(t["count"] > 0 for f in everything.values() for t in f)
    # A tag nobody carries is not listed at all.
    unused = {n for (n,) in conn.execute(
        """SELECT name FROM tags WHERE id NOT IN (SELECT tag_id FROM photo_tags)""")}
    assert unused.isdisjoint(t["name"] for f in everything.values() for t in f)

    scene = max(everything["scene"], key=lambda t: t["count"])
    faceted = client.get("/api/tags", params={"tags": str(scene["id"])}).json()["families"]
    in_filter = {i["id"] for i in client.get(
        "/api/photos", params={"tags": str(scene["id"]), "dupes": "all"}).json()["items"]}
    # Each count is the number of filtered photos carrying that tag.
    for family in faceted.values():
        for t in family:
            with_tag = {i["id"] for i in client.get(
                "/api/photos", params={"tags": str(t["id"]), "dupes": "all"}).json()["items"]}
            assert t["count"] == len(in_filter & with_tag) > 0
    # Other scene tags can't co-occur (max_tags 1), so only the selected one remains.
    assert [t["id"] for t in faceted["scene"]] == [scene["id"]]


def test_detail(client, conn):
    p = photo(conn, "IMG_0001.jpg")
    d = client.get(f"/api/photos/{p['id']}").json()
    assert d["camera"] == "Canon EOS R5"
    assert Path(d["raws"][0]["path"]).as_posix().endswith("trip/RAW/IMG_0001.CR3")
    assert {t["family"] for t in d["tags"]} == {"subject", "scene"}
    assert client.get(d["preview"]).status_code == 200

    dup = photo(conn, "IMG_0002.jpg")
    d2 = client.get(f"/api/photos/{dup['id']}").json()
    assert [x["rel_path"] for x in d2["duplicates"]] == ["trip/IMG_0002_edit.png"]
    assert client.get("/api/photos/9999").status_code == 404


def test_text_search(client):
    res = client.get("/api/search/text", params={"q": "red lanterns", "dupes": "all"}).json()
    assert res["total"] == 4
    scores = [i["score"] for i in res["items"]]
    assert scores == sorted(scores, reverse=True)


def test_similar_search_excludes_self_and_respects_filter(client, conn):
    a, b = photo(conn, "IMG_0002.jpg"), photo(conn, "IMG_0002_edit.png")
    res = client.get(f"/api/search/similar/{a['id']}", params={"dupes": "all"}).json()
    ids = [i["id"] for i in res["items"]]
    assert a["id"] not in ids
    assert ids[0] == b["id"]
    collapsed = client.get(f"/api/search/similar/{a['id']}").json()
    assert b["id"] not in [i["id"] for i in collapsed["items"]]  # the query's own duplicate

    tags = client.get("/api/tags").json()["families"]["scene"]
    t = next(t for t in tags if t["count"])
    res = client.get(f"/api/search/similar/{a['id']}", params={"tags": str(t["id"]), "dupes": "all"}).json()
    allowed = client.get("/api/photos", params={"tags": str(t["id"]), "dupes": "all"}).json()
    assert {i["id"] for i in res["items"]} <= {i["id"] for i in allowed["items"]}


def test_unmatched_raws(client):
    raws = client.get("/api/raws/unmatched").json()
    assert [Path(r["path"]).name for r in raws] == ["orphan.NEF"]


def test_exclude_tags(client, conn):
    tags = client.get("/api/tags").json()["families"]["scene"]
    scene = max(tags, key=lambda t: t["count"])
    everything = client.get("/api/photos", params={"dupes": "all"}).json()["total"]
    with_it = client.get("/api/photos", params={"tags": str(scene["id"]), "dupes": "all"}).json()["total"]
    without = client.get("/api/photos", params={"exclude_tags": str(scene["id"]), "dupes": "all"}).json()
    assert without["total"] == everything - with_it
    tagged = {r[0] for r in conn.execute("SELECT photo_id FROM photo_tags WHERE tag_id = ?", (scene["id"],))}
    assert tagged.isdisjoint(i["id"] for i in without["items"])

    # The excluded tag stays listed (count 0 in this view) so it can be switched off.
    listed = client.get("/api/tags", params={"exclude_tags": str(scene["id"])}).json()["families"]["scene"]
    entry = next(t for t in listed if t["id"] == scene["id"])
    assert entry["excluded"] and entry["count"] == 0
    # Excluding also applies to search, and including wins over excluding the same tag.
    res = client.get("/api/search/text", params={"q": "x", "exclude_tags": str(scene["id"]), "dupes": "all"}).json()
    assert res["total"] == everything - with_it
    both = client.get("/api/photos", params={"tags": str(scene["id"]), "exclude_tags": str(scene["id"]), "dupes": "all"})
    assert both.json()["total"] == with_it
    assert client.get("/api/photos", params={"exclude_tags": "x"}).status_code == 400


def test_items_carry_size_and_resolution(client):
    """For the compare view's size indicator (resolution, file size, compression)."""
    items = client.get("/api/photos", params={"dupes": "all"}).json()["items"]
    assert items and all(i["size_bytes"] > 0 and i["width"] and i["height"] for i in items)
