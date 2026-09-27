"""Captions and fixed tags: selections v5, captioning.py, search, export, the API."""

import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from riffle import captioning, selections
from riffle.export import caption_text
from riffle.geotag import Meta, tag
from riffle.server import create_app
from conftest import FakeClip, photo


@pytest.fixture
def client(indexed):
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        yield c


def pid(conn, name):
    return photo(conn, name)["id"]


def captions(client, *ids):
    return client.get("/api/captions", params={"ids": ",".join(map(str, ids))}).json()["captions"]


def listed(client, path="/api/photos", **params):
    return sorted(Path(i["rel_path"]).name for i in client.get(path, params={"dupes": "all", **params}).json()["items"])


def test_selections_migrate_from_v4(tmp_path):
    path = tmp_path / "selections.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        """CREATE TABLE flags (sha256 TEXT PRIMARY KEY, flag TEXT NOT NULL, source TEXT, rel_path TEXT, updated_at REAL NOT NULL);
           INSERT INTO flags VALUES ('abc', 'pick', 's', 'a.jpg', 1);
           PRAGMA user_version = 4;"""
    )
    old.commit()
    old.close()
    conn = selections.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert conn.execute("SELECT flag FROM flags").fetchone()[0] == "pick"
    assert conn.execute("SELECT COUNT(*) FROM captions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fixed_tags").fetchone()[0] == 0


def test_edit_bulk_and_undo(client, conn):
    a, b = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0003.png")
    res = client.post("/api/captions", json={"items": [{"id": a, "caption": " A boat at dusk ", "tags": ["boat", " dusk", "Boat", ""]}]})
    assert res.json()["previous"] == [{"id": a, "caption": None, "method": None, "edited": False, "tags": []}]
    got = captions(client, a, b)
    assert got[str(a)] == {"caption": "A boat at dusk", "method": "manual", "edited": True, "tags": ["boat", "dusk"], "rel_path": "trip/IMG_0001.jpg"}
    assert got[str(b)]["tags"] == [] and got[str(b)]["caption"] is None

    # Add to all (skips the photo that has it), rename onto an existing tag (merges), remove.
    added = client.post("/api/captions/tags", json={"ids": [a, b], "op": "add", "tag": "harbour"}).json()["previous"]
    assert sorted(p["id"] for p in added) == sorted([a, b])  # neither had it
    assert captions(client, a)[str(a)]["tags"] == ["boat", "dusk", "harbour"]
    client.post("/api/captions/tags", json={"ids": [a, b], "op": "rename", "tag": "dusk", "to": "BOAT"})
    assert captions(client, a)[str(a)]["tags"] == ["boat", "harbour"]
    removed = client.post("/api/captions/tags", json={"ids": [a, b], "op": "remove", "tag": "Harbour"}).json()["previous"]
    assert captions(client, a, b)[str(b)]["tags"] == [] and captions(client, a)[str(a)]["tags"] == ["boat"]

    # Undo: the previous values go back in.
    client.post("/api/captions", json={"items": removed})
    assert captions(client, a)[str(a)]["tags"] == ["boat", "harbour"]
    assert client.post("/api/captions/tags", json={"ids": [a], "op": "shout", "tag": "x"}).status_code == 400


def test_generated_modes_keep_edits(indexed, conn):
    path = indexed.selections_path
    a = pid(conn, "IMG_0001.jpg")
    selections.store_generated(conn, path, {a: {"caption": "first", "tags": ["x", "y"]}}, "joycaption", "add")
    selections.bulk_tags(conn, path, [a], "add", "mine")
    # add: the caption stays, new tags are appended
    selections.store_generated(conn, path, {a: {"caption": "second", "tags": ["y", "z"]}}, "wd", "add")
    got = selections.captions_for(conn, path, [a])[a]
    assert got["caption"] == "first" and got["tags"] == ["x", "y", "mine", "z"]
    # replace: generated ones go, typed ones stay; an edited caption stays
    selections.set_captions(conn, path, [{"id": a, "caption": "typed"}])
    selections.store_generated(conn, path, {a: {"caption": "third", "tags": ["w"]}}, "wd", "replace")
    got = selections.captions_for(conn, path, [a])[a]
    assert got["caption"] == "typed" and got["tags"] == ["mine", "w"]
    # replace_all: everything
    selections.store_generated(conn, path, {a: {"caption": "fourth", "tags": ["v"]}}, "wd", "replace_all")
    got = selections.captions_for(conn, path, [a])[a]
    assert got == {"caption": "fourth", "method": "wd", "edited": False, "tags": ["v"]}


def test_fixed_tag_filter_counts_and_detail(client, conn):
    a, b = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0003.png")
    client.post("/api/captions", json={"items": [{"id": a, "tags": ["harbour", "boat"], "caption": "Fishing boats"}, {"id": b, "tags": ["boat"]}]})
    assert listed(client, ftags=["boat"]) == ["IMG_0001.jpg", "IMG_0003.png"]
    assert listed(client, ftags=["boat", "Harbour"]) == ["IMG_0001.jpg"]
    assert "IMG_0001.jpg" not in listed(client, exclude_ftags=["harbour"])
    fixed = client.get("/api/tags").json()["fixed"]
    assert fixed == [{"tag": "boat", "count": 2, "excluded": False}, {"tag": "harbour", "count": 1, "excluded": False}]
    detail = client.get(f"/api/photos/{a}").json()
    assert detail["fixed_tags"] == ["harbour", "boat"] and detail["caption"]["text"] == "Fishing boats"


def test_search_puts_tag_and_caption_matches_first(client, conn):
    a, b = pid(conn, "IMG_0002.jpg"), pid(conn, "IMG_0003.png")
    client.post("/api/captions", json={"items": [{"id": a, "caption": "A red lighthouse on the coast"}, {"id": b, "tags": ["light_house", "lighthouse"]}]})
    items = client.get("/api/search/text", params={"q": "lighthouse", "dupes": "all"}).json()["items"]
    assert [(i["id"], i.get("name_match")) for i in items[:2]] == [(b, "tag"), (a, "caption")]
    off = client.get("/api/search/text", params={"q": "lighthouse", "dupes": "all", "names": "false"}).json()["items"]
    assert all("name_match" not in i for i in off)


def test_cheap_methods(client, conn, monkeypatch):
    a = pid(conn, "IMG_0001.jpg")
    res = client.post("/api/captioning", json={"ids": [a], "method": "riffle"}).json()
    assert res["done"] and res["photos"] == 1
    tags = captions(client, a)[str(a)]["tags"]
    detail = client.get(f"/api/photos/{a}").json()
    from riffle.clusters import CATCH_ALL

    expected = [
        t["name"].removeprefix("a ").removeprefix("an ")
        for t in sorted(detail["tags"], key=lambda t: t["id"])
        if t["name"] not in CATCH_ALL and not (t["family"] == "kind" and "photo" in t["name"])
    ]
    assert tags == expected and expected
    assert client.post("/api/captioning", json={"ids": [a], "method": "nope"}).status_code == 400
    phrases = client.post("/api/captioning", json={"ids": [a], "method": "phrases", "mode": "replace_all"})
    assert phrases.status_code == 200


def test_model_method_runs_as_a_job(client, conn, monkeypatch):
    a, b = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0003.png")
    monkeypatch.setattr(captioning, "_has", lambda m: True)
    monkeypatch.setattr(captioning, "_cached", lambda repo: True)
    seen = []

    def fake_load(options):
        def run(images):
            seen.extend(im.size for im in images)
            return [{"tags": ["1girl", "Blue Sky", "meta:foo"]} for _ in images]

        return run, lambda: None

    monkeypatch.setitem(captioning.LOADERS, "wd", fake_load)
    status = client.get("/api/captioning").json()
    assert {m["key"] for m in status["methods"]} == {"riffle", "phrases", "joycaption", "wd"}
    res = client.post("/api/captioning", json={"ids": [a, b], "method": "wd", "mode": "add"})
    assert res.status_code == 200 and res.json()["done"] is False
    deadline = time.time() + 10
    while (job := client.get("/api/captioning").json()["job"])["running"] and time.time() < deadline:
        time.sleep(0.05)
    assert job["error"] is None and job["result"]["photos"] == 2
    assert len(seen) == 2 and max(seen[0]) <= 1600  # the preview, not the original
    assert captions(client, a)[str(a)]["tags"] == ["1girl", "Blue Sky", "meta:foo"]


def test_taglist_filters_by_list_alias_and_spelling(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text('1girl,0,100,"1girls,sole_female"\nblue_sky,0,50,""\ntree,0,40,""\nclose-up,0,30,""\n')
    tl = captioning.TagList.load(p)
    assert tl.filter(["1girls", "Blue Sky", "trees", "close_up", "serene", "sole female"]) == ["1girl", "blue_sky", "tree", "close-up", "1girl"]
    wd = tmp_path / "selected_tags.csv"
    wd.write_text("tag_id,name,category,count\n1,general,9,1\n2,solo,0,1\n3,hatsune_miku,4,1\n")
    assert captioning.TagList.load(wd).filter(["general", "solo", "hatsune miku"]) == ["solo", "hatsune_miku"]
    txt = tmp_path / "mine.txt"
    txt.write_text("# my keywords\nharbour\nSunset\n")
    assert captioning.TagList.load(txt).filter(["sunsets", "harbours", "boat"]) == ["Sunset", "harbour"]


def test_split_tags():
    assert captioning.split_tags("Boats, blue_sky,\nSunset.", booru=False) == ["boats", "blue sky", "sunset"]
    many = ", ".join(["photograph", *(f"k{i}" for i in range(30)), "k1"])
    assert captioning.split_tags(many, booru=False) == [f"k{i}" for i in range(captioning.KEYWORD_MAX)]
    assert captioning.split_tags("copyright:original, meta:photoshop_(medium), character:miku, blue sky", booru=True) == ["miku", "blue_sky"]
    assert captioning.split_tags("artist:x, solo", booru=True, keep_prefixed=True) == ["x", "solo"]


def test_caption_text_styles():
    assert caption_text("A boat.", ["blue_sky", "boat"], "tags", False) == "blue_sky, boat"
    assert caption_text("A boat.", ["blue_sky", "boat"], "tags", True) == "blue sky, boat"
    assert caption_text("A boat.", [], "both", False) == "A boat."
    assert caption_text("A boat.", ["boat"], "both", False) == "A boat.\nboat"


def xmp_of(path: Path) -> str:
    data = path.read_bytes()
    start = data.find(b"<x:xmpmeta")
    return data[start : data.find(b"</x:xmpmeta>") + 12].decode() if start >= 0 else ""


def test_embed_caption_and_position_in_one_pass(tmp_path):
    src = tmp_path / "a.jpg"
    Image.new("RGB", (64, 48), "red").save(src, exif=Image.Exif())
    t = tag(src, meta=Meta(59.3, 18.1, None, "A red <square>", ("red", "square")))
    out = tmp_path / "out.jpg"
    out.write_bytes(t.head + src.read_bytes()[t.rest :])
    assert t.method == "exif"
    with Image.open(out) as im:
        im.load()
        assert im.getexif().get_ifd(0x8825)  # GPS
    x = xmp_of(out)
    assert "A red &lt;square&gt;" in x and "<rdf:li>square</rdf:li>" in x and "GPSLatitude" not in x
    png = tmp_path / "b.png"
    Image.new("RGB", (8, 8)).save(png)
    tp = tag(png, meta=Meta(description="dark", keywords=("night",)))
    pout = tmp_path / "out.png"
    pout.write_bytes(tp.head + png.read_bytes()[tp.rest :])
    with Image.open(pout) as im:
        im.load()
        assert "<rdf:li>night</rdf:li>" in im.info.get("XML:com.adobe.xmp", "")


def export(client, folder, ids, **extra):
    body = {"folder": str(folder), "name": "out", "photo_ids": ids, "add_location": False, **extra}
    assert client.post("/api/export", json=body).status_code == 200, body
    deadline = time.time() + 20
    while (status := client.get("/api/export").json())["running"] and time.time() < deadline:
        time.sleep(0.05)
    assert status["error"] is None, status["error"]
    return folder / "out", status["result"]


def test_export_captions_in_each_format(client, conn, tmp_path, cfg):
    a, b = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0003.png")
    client.post("/api/captions", json={"items": [{"id": a, "caption": "A harbour at dusk", "tags": ["blue_sky", "boat"]}]})
    original = (cfg.sources[0] / "trip" / "IMG_0001.jpg").read_bytes()

    out, res = export(client, tmp_path / "txt", [a, b], captions="txt", caption_text="both", underscores=True)
    assert (out / "IMG_0001.txt").read_text() == "A harbour at dusk\nblue sky, boat\n"
    assert not (out / "IMG_0003.txt").exists() and res["captioned"] == 1 and res["without_caption"] == 1

    out, _ = export(client, tmp_path / "embed", [a], captions="embed")
    assert "<rdf:li>blue_sky</rdf:li>" in xmp_of(out / "IMG_0001.jpg") and not (out / "IMG_0001.xmp").exists()
    assert (cfg.sources[0] / "trip" / "IMG_0001.jpg").read_bytes() == original  # the original is untouched

    out, _ = export(client, tmp_path / "xmp", [a], captions="xmp")
    assert "A harbour at dusk" in (out / "IMG_0001.xmp").read_text()
    assert "<rdf:li>" not in xmp_of(out / "IMG_0001.jpg")

    out, res = export(client, tmp_path / "jsonl", [a, b], captions="jsonl")
    rows = [json.loads(line) for line in (out / "metadata.jsonl").read_text().splitlines()]
    assert rows == [{"file_name": "IMG_0001.jpg", "text": "A harbour at dusk", "tags": ["blue_sky", "boat"]}]

    assert client.post("/api/export", json={"folder": str(tmp_path), "name": "x", "photo_ids": [a], "captions": "pdf"}).status_code == 400


def test_profile_copy_includes_captions(client, conn):
    a = pid(conn, "IMG_0001.jpg")
    client.post("/api/captions", json={"items": [{"id": a, "caption": "kept", "tags": ["t"]}]})
    slug = client.post("/api/profiles", json={"name": "Copy", "copy_from": "default", "parts": ["captions"]}).json()["slug"]
    client.post(f"/api/profiles/{slug}/activate", json={})
    assert captions(client, a)[str(a)]["caption"] == "kept"
    empty = client.post("/api/profiles", json={"name": "Empty", "copy_from": "default", "parts": ["flags"]}).json()["slug"]
    client.post(f"/api/profiles/{empty}/activate", json={})
    assert captions(client, a)[str(a)]["tags"] == []
