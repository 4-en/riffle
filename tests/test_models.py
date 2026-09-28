"""Choosing the AI model (models.py, /api/models, /api/model): prepared by an index run,
then switched in place; the stack similarity fitted to it."""

import sqlite3
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from riffle import models
from riffle.config import load_config, set_model
from riffle.embed import embedding_paths
from riffle.server import create_app
from conftest import FakeClip, fake_index


def wait_index(c, timeout=30):
    deadline = time.time() + timeout
    while c.get("/api/index").json()["running"]:
        assert time.time() < deadline, "index did not finish"
        time.sleep(0.05)
    status = c.get("/api/index").json()
    assert status["error"] is None, status
    return status


@pytest.fixture
def client(indexed):
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text, index_runner=fake_index)) as c:
        yield c


def by_key(listing):
    return {m["key"]: m for m in listing["models"]}


def test_the_catalogue(client):
    listing = client.get("/api/models").json()
    got = by_key(listing)
    assert {"fast", "default", "best", "bioclip-2.5", "bioclip-2"} <= set(got)
    assert got["bioclip-2.5"]["name"] == "hf-hub:imageomics/bioclip-2.5-vith14" and got["bioclip-2.5"]["group"] == "nature"
    # The test library's model is not in the catalogue: listed as the one in use.
    assert got["custom"]["in_use"] and got["custom"]["embedded"] == listing["photos"] == 4
    assert got["default"]["embedded"] == 0 and not got["default"]["in_use"]


def test_switching_models(client, indexed):
    before = indexed.model.model_id
    assert client.post("/api/model", json={"name": "ViT-B-16", "pretrained": "dfn2b"}).json() == {"started": True}
    status = wait_index(client)
    assert status["result"]["stack_similarity"] == 0.90 and "now using Faster" in status["lines"]
    got = by_key(client.get("/api/models").json())
    assert got["fast"]["in_use"] and got["fast"]["embedded"] == 4
    assert indexed.model.name == "ViT-B-16" and indexed.stacks.min_similarity == 0.90
    assert client.get("/api/tags").json()["model_id"] == indexed.model.model_id != before
    assert embedding_paths(indexed)[0].exists()
    # Search, tags and similar photos use the new model's data.
    assert client.get("/api/search/text", params={"q": "red", "dupes": "all"}).json()["total"] == 4
    assert client.get("/api/tags").json()["families"]
    # Back to the first: already embedded, so only a quick run.
    assert client.post("/api/model", json={"name": "fake", "pretrained": "test"}).json()["started"]
    wait_index(client)
    assert indexed.model.model_id == before
    assert client.post("/api/model", json={"name": "fake", "pretrained": "test"}).json() == {"started": False, "in_use": True}


def test_the_current_model_stays_until_the_new_one_is_ready(indexed):
    gate = threading.Event()

    def slow_index(cfg, progress=None, report=print):
        gate.wait(10)
        fake_index(cfg, progress, report)

    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text, index_runner=slow_index)) as c:
        before = indexed.model.model_id
        assert c.post("/api/model", json={"name": "ViT-B-16", "pretrained": "dfn2b"}).json()["started"]
        assert c.get("/api/tags").json()["model_id"] == before  # still the old one
        assert c.get("/api/models").json()["preparing"] == {"name": "ViT-B-16", "pretrained": "dfn2b"}
        assert c.post("/api/model", json={"name": "ViT-L-14-quickgelu", "pretrained": "dfn2b"}).status_code == 409
        gate.set()
        wait_index(c)
        assert c.get("/api/tags").json()["model_id"] != before


def test_hub_models_have_no_pretrained_name(client, indexed):
    client.post("/api/model", json={"name": "hf-hub:imageomics/bioclip-2", "pretrained": "whatever"})
    wait_index(client)
    assert indexed.model.name == "hf-hub:imageomics/bioclip-2" and indexed.model.pretrained == ""
    assert by_key(client.get("/api/models").json())["bioclip-2"]["in_use"]
    assert client.post("/api/model", json={"name": " "}).status_code == 400


def test_stack_similarity_from_camera_bursts():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, status TEXT, camera TEXT, taken_at TEXT)")
    rng = np.random.default_rng(0)
    rows, vecs = [], []
    for k in range(80):  # 80 bursts of two shots one second apart, an hour between bursts
        base = rng.normal(size=16)
        for s in range(2):
            v = base + 0.1 * rng.normal(size=16)
            vecs.append(v / np.linalg.norm(v))
            rows.append((len(rows) + 1, "ok", "Cam", f"2024:05:01 {k // 60 + 10:02d}:{k % 60:02d}:0{s}"))
    conn.executemany("INSERT INTO photos VALUES (?, ?, ?, ?)", rows)
    E, ids = np.array(vecs, np.float32), np.arange(1, len(rows) + 1)
    pair = [float(E[i] @ E[i + 1]) for i in range(0, len(E), 2)]
    got = models.calibrate_stack_similarity(conn, E, ids)
    assert got == round(float(np.percentile(pair, models.BURST_PERCENTILE)), 3)
    assert models.calibrate_stack_similarity(conn, E[:40], ids[:40]) is None  # too few bursts: keep the default


def test_the_config_file_keeps_the_rest(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("# mine\nsources: []\nmodel:\n  name: ViT-L-14-quickgelu  # note\n  pretrained: dfn2b\n  device: cuda\n", encoding="utf-8")
    cfg = load_config(path)
    set_model(cfg, "hf-hub:imageomics/bioclip-2.5-vith14", "", 0.9)
    text = path.read_text(encoding="utf-8")
    assert "# mine" in text and "device: cuda" in text and "# note" in text
    again = load_config(path)
    assert (again.model.name, again.model.pretrained, again.model.device) == ("hf-hub:imageomics/bioclip-2.5-vith14", "", "cuda")
    assert again.stacks.min_similarity == 0.9
