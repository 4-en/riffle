import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from riffle.config import load_config, set_sources
from riffle.server import create_app
from conftest import FakeClip, fake_index

CONFIG = """\
# my archive
sources:
  - photos   # relative entry
exclude:
  - "**/.Trashes/**"

model:
  name: fake
  pretrained: test
"""


@pytest.fixture
def file_cfg(archive_dir):
    (archive_dir / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (archive_dir / "vocabulary.yaml").write_text("subject: [a, b]\nscene: [c, d]\n", encoding="utf-8")
    return load_config(archive_dir / "config.yaml")


def test_set_sources_keeps_rest_of_file(file_cfg, archive_dir, tmp_path):
    extra = tmp_path / "more"
    extra.mkdir()
    set_sources(file_cfg, [*file_cfg.sources, extra])
    text = (archive_dir / "config.yaml").read_text(encoding="utf-8")
    assert text.startswith("# my archive\nsources:\n")
    assert '  - "photos"\n' in text  # original spelling kept
    assert "\nexclude:" in text and "model:" in text
    reloaded = load_config(archive_dir / "config.yaml")
    assert reloaded.sources == [archive_dir / "photos", extra]
    assert reloaded.exclude == ["**/.Trashes/**"]

    set_sources(file_cfg, [])
    assert yaml.safe_load((archive_dir / "config.yaml").read_text(encoding="utf-8"))["sources"] == []


@pytest.fixture
def client(file_cfg):
    app = create_app(file_cfg, text_encoder=FakeClip().encode_text, index_runner=fake_index)
    with TestClient(app) as c:
        yield c


def wait_for_index(client, path="/api/index", timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(path).json()
        if not status["running"]:
            return status
        time.sleep(0.05)
    raise AssertionError("index did not finish")


def test_index_from_api(client):
    assert client.get("/api/tags").json()["photos"] == 0
    status = client.post("/api/index", json={}).json()
    assert status["running"] or status["finished_at"]
    status = wait_for_index(client)
    assert status["error"] is None
    assert status["lines"] == ["indexed 5 images"]
    assert client.get("/api/tags").json()["photos"] == 4
    sources = client.get("/api/sources").json()["sources"]
    assert sources[0]["photos"] == 4


def test_add_and_remove_folder(client, archive_dir, tmp_path):
    from PIL import Image

    new = tmp_path / "elsewhere"
    new.mkdir()
    Image.new("RGB", (64, 48), "green").save(new / "green.jpg")

    assert client.post("/api/sources", json={"path": str(new)}).status_code == 200
    wait_for_index(client)
    assert [s["path"] for s in client.get("/api/sources").json()["sources"]] == [
        str(archive_dir / "photos"),
        str(new),
    ]
    assert client.get("/api/tags").json()["photos"] == 5
    assert str(new) in yaml.safe_load((archive_dir / "config.yaml").read_text(encoding="utf-8"))["sources"]

    # Already covered, not a folder, relative, or wrong content type.
    assert client.post("/api/sources", json={"path": str(new)}).status_code == 409
    assert client.post("/api/sources", json={"path": str(new / "green.jpg")}).status_code == 400
    assert client.post("/api/sources", json={"path": "relative/dir"}).status_code == 400
    assert client.post("/api/sources", content=f'{{"path": "{new}"}}').status_code == 415

    resp = client.request("DELETE", "/api/sources", json={"path": str(new)})
    assert resp.status_code == 200
    wait_for_index(client)
    assert client.get("/api/tags").json()["photos"] == 4


def test_adding_a_parent_replaces_child_sources(client, archive_dir):
    client.post("/api/index", json={})
    wait_for_index(client)
    before = {i["rel_path"]: i["id"] for i in client.get("/api/photos", params={"dupes": "all"}).json()["items"]}

    assert client.post("/api/sources", json={"path": str(archive_dir)}).status_code == 200
    wait_for_index(client)
    sources = client.get("/api/sources").json()["sources"]
    assert [s["path"] for s in sources] == [str(archive_dir)]
    after = {i["rel_path"]: i["id"] for i in client.get("/api/photos", params={"dupes": "all"}).json()["items"]}
    # Same photos, same ids, now relative to the parent; data/ thumbnails are not indexed.
    assert after == {f"photos/{k}": v for k, v in before.items()}


def test_browse(client, archive_dir):
    root = client.get("/api/fs", params={"path": str(archive_dir / "photos")}).json()
    assert [d["name"] for d in root["dirs"]] == ["trip"]  # hidden .Trashes skipped
    assert root["source"] == str(archive_dir / "photos")
    trip = client.get("/api/fs", params={"path": str(archive_dir / "photos" / "trip")}).json()
    assert trip["images"] == 5 and [d["name"] for d in trip["dirs"]] == ["RAW"]
    assert trip["parent"] == str(archive_dir / "photos")
    assert client.get("/api/fs", params={"path": str(archive_dir / "nope")}).status_code == 404
    assert client.get("/api/fs").json()["path"] == str(Path.home())


def test_adding_a_folder_imports_exported_captions(client, tmp_path):
    """Captions, tags and read text beside the photos, in the formats export writes, come
    along when the folder is added; what a photo already has is kept."""
    import json

    from PIL import Image

    from riffle.geotag import xmp_packet

    new = tmp_path / "dataset"
    new.mkdir()
    for name, colour in (("a", "red"), ("b", "blue"), ("c", "yellow")):
        Image.new("RGB", (64, 48), colour).save(new / f"{name}.jpg")
    (new / "a.xmp").write_bytes(xmp_packet(description="A red wall\n\nEXIT", keywords=["wall", "red"], text="EXIT"))
    (new / "b.txt").write_text("blue, plain\n", encoding="utf-8")
    (new / "metadata.jsonl").write_text(json.dumps({"file_name": "c.jpg", "text": "A yellow card", "tags": []}) + "\n", encoding="utf-8")

    assert client.post("/api/sources", json={"path": str(new)}).status_code == 200
    status = wait_for_index(client)
    assert any("imported from" in line and "2 captions, 2 tag lists, 1 read texts" in line for line in status["lines"]), status["lines"]
    items = client.get("/api/photos", params={"dupes": "all"}).json()["items"]
    ids = {Path(i["rel_path"]).name: i["id"] for i in items if Path(i["rel_path"]).name in ("a.jpg", "b.jpg", "c.jpg")}
    got = client.get("/api/captions", params={"ids": ",".join(str(i) for i in ids.values())}).json()["captions"]
    a, b, c = (got[str(ids[n])] for n in ("a.jpg", "b.jpg", "c.jpg"))
    assert a["caption"] == "A red wall" and a["tags"] == ["wall", "red"] and a["text"] == "EXIT"
    assert b["caption"] is None and b["tags"] == ["blue", "plain"]
    assert c["caption"] == "A yellow card"

    # Asked again: nothing already there is replaced.
    client.post("/api/captions", json={"items": [{"id": ids["a.jpg"], "caption": "My own words"}]})
    assert client.post("/api/sources/import", json={"path": str(new)}).status_code == 200
    status = wait_for_index(client, "/api/sources/import")
    assert status["result"] and status["result"][0]["captions"] == 0 and status["result"][0]["photos"] == 3
    again = client.get("/api/captions", params={"ids": str(ids["a.jpg"])}).json()["captions"][str(ids["a.jpg"])]
    assert again["caption"] == "My own words"
    assert client.post("/api/sources/import", json={"path": str(tmp_path)}).status_code == 404
