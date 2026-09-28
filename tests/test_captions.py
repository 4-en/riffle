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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == selections.VERSION
    assert conn.execute("SELECT flag FROM flags").fetchone()[0] == "pick"
    assert conn.execute("SELECT COUNT(*) FROM captions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fixed_tags").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM photo_text").fetchone()[0] == 0


def test_edit_bulk_and_undo(client, conn):
    a, b = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0003.png")
    res = client.post("/api/captions", json={"items": [{"id": a, "caption": " A boat at dusk ", "tags": ["boat", " dusk", "Boat", ""]}]})
    NO_TEXT = {"text": None, "translation": "", "language": None, "text_method": None, "text_edited": False}
    assert res.json()["previous"] == [{"id": a, "caption": None, "method": None, "edited": False, "tags": []} | NO_TEXT]
    got = captions(client, a, b)
    assert got[str(a)] == {"caption": "A boat at dusk", "method": "manual", "edited": True, "tags": ["boat", "dusk"], "rel_path": "trip/IMG_0001.jpg", "tokens": captioning.token_count("boat, dusk")} | NO_TEXT
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
    assert {k: got[k] for k in ("caption", "method", "edited", "tags")} == {"caption": "fourth", "method": "wd", "edited": False, "tags": ["v"]}


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
    one = client.post("/api/captioning", json={"ids": [a], "method": "riffle", "mode": "replace_all", "limit_kind": "tags", "limit": 1})
    assert one.status_code == 200 and captions(client, a)[str(a)]["tags"] == expected[:1]
    assert client.post("/api/captioning", json={"ids": [a], "method": "riffle", "limit_kind": "words"}).status_code == 400
    assert client.post("/api/captioning", json={"ids": [a], "method": "nope"}).status_code == 400
    phrases = client.post("/api/captioning", json={"ids": [a], "method": "phrases", "mode": "replace_all"})
    assert phrases.status_code == 200


def test_model_method_runs_as_a_job(client, conn, monkeypatch):
    a, b = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0003.png")
    monkeypatch.setattr(captioning, "_has", lambda m: True)
    monkeypatch.setattr(captioning, "_cached", lambda repo: True)
    seen = []

    def fake_load(options):
        def run(images, ids):
            seen.extend(im.size for im in images)
            return [{"tags": ["1girl", "Blue Sky", "meta:foo"]} for _ in images]

        return run, lambda: None

    monkeypatch.setitem(captioning.LOADERS, "wd", fake_load)
    status = client.get("/api/captioning").json()
    assert {m["key"] for m in status["methods"]} == {"riffle", "phrases", "joycaption", "ocr", "qwen", "wd"}
    res = client.post("/api/captioning", json={"ids": [a, b], "method": "wd", "mode": "add"})
    assert res.status_code == 200 and res.json()["done"] is False
    deadline = time.time() + 10
    while (job := client.get("/api/captioning").json()["job"])["running"] and time.time() < deadline:
        time.sleep(0.05)
    assert job["error"] is None and job["result"]["photos"] == 2
    assert len(seen) == 2 and max(seen[0]) <= 1600  # the preview, not the original
    assert captions(client, a)[str(a)]["tags"] == ["1girl", "Blue Sky", "meta:foo"]
    # booru tags are stored with spaces unless asked to keep the underscores
    monkeypatch.setitem(captioning.LOADERS, "wd", lambda o: (lambda images, ids: [{"tags": ["long_hair", "^_^"]} for _ in ids], lambda: None))
    for underscores, expected in ((False, ["long hair", "^_^"]), (True, ["long_hair", "^_^"])):
        client.post("/api/captioning", json={"ids": [b], "method": "wd", "mode": "replace_all", "options": {"underscores": underscores}})
        while client.get("/api/captioning").json()["job"]["running"]:
            time.sleep(0.05)
        assert captions(client, b)[str(b)]["tags"] == expected


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


def test_booru_style():
    tags = ["long_hair", "blue sky", "^_^", ">_<", "1girl"]
    assert captioning.booru_style(tags) == ["long hair", "blue sky", "^_^", ">_<", "1girl"]
    assert captioning.booru_style(tags, underscores=True) == ["long_hair", "blue_sky", "^_^", ">_<", "1girl"]


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


# ---- text in the photo (OCR) -----------------------------------------------------------


def test_parse_ocr():
    p = captioning.parse_ocr
    assert p('{"text": "欢迎来到北京", "language": "Chinese", "english": "Welcome to Beijing"}') == {
        "text": "欢迎来到北京", "translation": "Welcome to Beijing", "language": "Chinese"
    }
    fenced = '```json\n{"text": "EXIT", "language": "English", "english": "EXIT"}\n```'
    assert p(fenced) == {"text": "EXIT", "translation": "", "language": "English"}  # English: no translation
    assert p('{"text": ""}') == {"text": "", "translation": "", "language": None}
    assert p("No readable text.")["text"] == "" and p("none")["text"] == ""
    assert p("OPEN 9-5\nClosed Sundays")["text"] == "OPEN 9-5\nClosed Sundays"  # prose: the whole answer


def test_text_modes_edits_and_undo(indexed, conn):
    path = indexed.selections_path
    a, b = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0003.png")
    zh = {"text": "欢迎来到北京", "translation": "Welcome to Beijing", "language": "Chinese"}
    selections.store_generated(conn, path, {a: {"text": zh}, b: {"text": {"text": ""}}}, "ocr", "add")
    got = selections.captions_for(conn, path, [a, b])
    assert (got[a]["text"], got[a]["translation"], got[a]["language"]) == ("欢迎来到北京", "Welcome to Beijing", "Chinese")
    assert got[b]["text"] == ""  # read, nothing found: not None
    # add: read photos are not read again; replace keeps an edited text
    selections.store_generated(conn, path, {b: {"text": {"text": "late"}}}, "ocr", "add")
    assert selections.captions_for(conn, path, [b])[b]["text"] == ""
    prev = selections.set_captions(conn, path, [{"id": a, "text": "欢迎来到北京!"}])
    selections.store_generated(conn, path, {a: {"text": {"text": "other"}}}, "ocr", "replace")
    got = selections.captions_for(conn, path, [a])[a]
    assert got["text"] == "欢迎来到北京!" and got["text_edited"] and got["translation"] == "Welcome to Beijing"
    # translating again changes only the translation, and keeps the edit mark
    selections.store_generated(conn, path, {a: {"translation_only": "Welcome to Beijing!"}}, "ocr", "add")
    got = selections.captions_for(conn, path, [a])[a]
    assert got["translation"] == "Welcome to Beijing!" and got["text_edited"]
    # undo the typed edit: the generated text and its marks come back
    selections.set_captions(conn, path, list(prev.values()))
    got = selections.captions_for(conn, path, [a])[a]
    assert got["text"] == "欢迎来到北京" and not got["text_edited"] and got["text_method"] == "ocr"
    selections.set_captions(conn, path, [{"id": b, "text": None}])  # forget: never read
    assert selections.captions_for(conn, path, [b])[b]["text"] is None


def test_search_finds_text_in_the_photo(client, conn):
    a, b, c = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0002.jpg"), pid(conn, "IMG_0003.png")
    client.post("/api/captions", json={"items": [
        {"id": a, "text": "欢迎来到北京", "translation": "Welcome to Beijing"},
        {"id": b, "caption": "Beijing at night"},
        {"id": c, "tags": ["北京"]},
    ]})
    items = client.get("/api/search/text", params={"q": "北京", "dupes": "all"}).json()["items"]
    assert [(i["id"], i.get("name_match")) for i in items[:2]] == [(c, "tag"), (a, "text")]
    items = client.get("/api/search/text", params={"q": "beijing", "dupes": "all"}).json()["items"]
    assert [(i["id"], i.get("name_match")) for i in items[:2]] == [(b, "caption"), (a, "text")]
    detail = client.get(f"/api/photos/{a}").json()
    assert detail["text"] == {"text": "欢迎来到北京", "translation": "Welcome to Beijing", "language": None}


def test_ocr_job_and_retranslate(client, conn, monkeypatch):
    a, b = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0003.png")
    monkeypatch.setattr(captioning, "_has", lambda m: True)
    monkeypatch.setattr(captioning, "_cuda", lambda: True)
    calls = []

    def fake_load(options):
        def run(images, ids):
            calls.append((len(images), list(ids), options.get("texts")))
            if options.get("retranslate"):
                return [{"translation_only": f"EN:{options['texts'][str(i)]}"} for i in ids]
            return [{"text": captioning.parse_ocr('{"text": "出口", "language": "Chinese", "english": "Exit"}' if i == a else '{"text": ""}')} for i in ids]

        return run, lambda: None

    monkeypatch.setitem(captioning.LOADERS, "ocr", fake_load)

    def run(body):
        assert client.post("/api/captioning", json=body).status_code == 200
        while client.get("/api/captioning").json()["job"]["running"]:
            time.sleep(0.05)

    run({"ids": [a, b], "method": "ocr"})
    got = captions(client, a, b)
    assert (got[str(a)]["text"], got[str(a)]["translation"]) == ("出口", "Exit") and got[str(b)]["text"] == ""
    client.post("/api/captions", json={"items": [{"id": a, "text": "出口 2"}]})
    run({"ids": [a, b], "method": "ocr", "options": {"retranslate": True}})
    assert calls[-1] == (0, [a], {str(a): "出口 2"})  # only photos with text; no images loaded
    assert captions(client, a)[str(a)]["translation"] == "EN:出口 2"
    res = client.post("/api/captioning", json={"ids": [b], "method": "ocr", "options": {"retranslate": True}})
    assert res.status_code == 400


def test_export_with_text(client, conn, tmp_path):
    a, b = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0003.png")
    client.post("/api/captions", json={"items": [{"id": a, "caption": "A gate", "text": "出口", "translation": "Exit"}, {"id": b, "text": "Only text"}]})
    out, _ = export(client, tmp_path / "t", [a], captions="txt", caption_text="caption", with_text=True)
    assert (out / "IMG_0001.txt").read_text() == "A gate\n出口\nExit\n"
    out, _ = export(client, tmp_path / "x", [a, b], captions="xmp", with_text=True)
    assert "A gate\n\n出口\nExit" in (out / "IMG_0001.xmp").read_text()
    assert "Only text" in (out / "IMG_0003.xmp").read_text()  # read text alone is enough
    out, _ = export(client, tmp_path / "j", [a], captions="jsonl")
    row = json.loads((out / "metadata.jsonl").read_text())
    assert row["ocr_text"] == "出口" and row["ocr_translation"] == "Exit"


def test_selections_migrate_from_v5(tmp_path):
    path = tmp_path / "selections.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        """CREATE TABLE captions (sha256 TEXT PRIMARY KEY, text TEXT NOT NULL, method TEXT, edited INTEGER NOT NULL DEFAULT 0,
             source TEXT, rel_path TEXT, updated_at REAL NOT NULL);
           INSERT INTO captions VALUES ('abc', 'kept', 'manual', 1, 's', 'a.jpg', 1);
           PRAGMA user_version = 5;"""
    )
    old.commit()
    old.close()
    conn = selections.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == selections.VERSION
    assert conn.execute("SELECT text FROM captions").fetchone()[0] == "kept"
    assert conn.execute("SELECT COUNT(*) FROM photo_text").fetchone()[0] == 0


def test_parse_combined():
    answer = """```json
{"caption": "A red sign in Beijing.", "keywords": ["Sign", "photograph", "city"], "text": "出口", "language": "Chinese", "english": "Exit"}
```"""
    assert captioning.parse_combined(answer) == {
        "caption": "A red sign in Beijing.",
        "tags": ["sign", "city"],
        "text": {"text": "出口", "translation": "Exit", "language": "Chinese"},
    }
    none = captioning.parse_combined('{"caption": "A beetle.", "keywords": "beetle", "text": "", "language": "", "english": ""}')
    assert none["text"] == {"text": "", "translation": "", "language": None} and none["tags"] == ["beetle"]
    assert captioning.parse_combined("Just prose.") == {"caption": "Just prose."}


def test_ocr_text_check_skips_photos_without_text(client, conn, monkeypatch):
    a, b, c = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0002.jpg"), pid(conn, "IMG_0003.png")
    monkeypatch.setattr(captioning, "_has", lambda m: True)
    monkeypatch.setattr(captioning, "_cuda", lambda: True)
    read = []

    def fake_load(options):
        def run(images, ids):
            read.extend(ids)
            return [{"text": {"text": f"T{i}"}} for i in ids]

        return run, lambda: None

    monkeypatch.setitem(captioning.LOADERS, "ocr", fake_load)
    marks = []  # what the check says for the photos it is asked about, per call
    monkeypatch.setattr(captioning, "text_likely", lambda E, *v: [bool(x) for x in marks.pop(0)])

    def go(ids, **options):
        res = client.post("/api/captioning", json={"ids": ids, "method": "ocr", "options": options}).json()
        while client.get("/api/captioning").json()["job"]["running"]:
            time.sleep(0.05)
        return res

    marks.append([1, 0, 0])  # only IMG_0001 looks like it has text
    res = go([a, b, c], prefilter=True)
    assert res["skipped"] == 2 and read == [a]
    got = captions(client, a, b, c)
    assert got[str(a)]["text"] == f"T{a}"
    assert got[str(b)]["text"] == "" and got[str(b)]["text_method"] == "clip"  # skipped, marked as such

    # Without the check, skipped photos count as unread and are read (add mode).
    res = go([b, c], prefilter=False)
    assert read == [a, b, c] and captions(client, b)[str(b)]["text"] == f"T{b}"

    # Photos that already have text are never left out by the check (it isn't asked).
    res = client.post("/api/captioning", json={"ids": [a], "method": "ocr", "mode": "replace_all"}).json()
    while client.get("/api/captioning").json()["job"]["running"]:
        time.sleep(0.05)
    assert res["skipped"] == 0 and read[-1] == a and not marks


def test_parse_ocr_of_an_answer_cut_off():
    cut = '{\n  "text": "Delineation Oppa Trachta\\nhwarest Rijt: Admiralen Grewe Gustaff Otto St'
    got = captioning.parse_ocr(cut)
    assert got["text"] == "Delineation Oppa Trachta\nhwarest Rijt: Admiralen Grewe Gustaff Otto St"
    both = captioning.parse_ocr('{"text": "Anno 1656", "language": "Swedish", "english": "Year 16')
    assert both == {"text": "Anno 1656", "translation": "Year 16", "language": "Swedish"}



def test_new_tags_at_the_start_and_limits(client, conn, indexed):
    path = indexed.selections_path
    a, b = pid(conn, "IMG_0001.jpg"), pid(conn, "IMG_0003.png")
    client.post("/api/captions", json={"items": [{"id": a, "tags": ["kept"]}]})
    # A trigger word first in every photo.
    client.post("/api/captions/tags", json={"ids": [a, b], "op": "add", "tag": "mychar", "at": "start"})
    assert captions(client, a)[str(a)]["tags"] == ["mychar", "kept"]
    assert client.post("/api/captions/tags", json={"ids": [a], "op": "add", "tag": "x", "at": "middle"}).status_code == 400
    # Generated tags at the start, in the model's order, until the limit; kept tags stay.
    new = [f"tag{i}" for i in range(10)]
    selections.store_generated(conn, path, {a: {"tags": new}}, "wd", "add", at="start", limit=("tags", 5))
    assert selections.captions_for(conn, path, [a])[a]["tags"] == ["tag0", "tag1", "tag2", "mychar", "kept"]
    long = ["long hair", "looking at viewer", "animal ears", "blue sky", "outdoors", "smile"] * 4
    long = [f"{t} {i}" for i, t in enumerate(long)]
    selections.store_generated(conn, path, {b: {"tags": long}}, "wd", "replace_all", limit=("tokens", 30), count_tokens=captioning.token_count)
    tags = selections.captions_for(conn, path, [b])[b]["tags"]
    assert captioning.token_count(", ".join(tags)) <= 30 < captioning.token_count(", ".join(long[: len(tags) + 1]))
    assert tags == long[: len(tags)]
    assert captions(client, b)[str(b)]["tokens"] == captioning.token_count(", ".join(tags))
