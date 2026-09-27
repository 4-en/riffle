"""The "Similar" grouping: clusters.py and group=similar in the API."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from riffle import clusters
from riffle.embed import load_embeddings, save_embeddings
from riffle.server import create_app
from conftest import FakeClip, photo

DIM = 16


def blob(centre_axis: int, n: int, spread: float, rng) -> np.ndarray:
    X = np.zeros((n, DIM))
    X[:, centre_axis] = 1.0
    X += spread * rng.normal(size=(n, DIM))
    return X / np.linalg.norm(X, axis=1, keepdims=True)


@pytest.fixture
def data():
    rng = np.random.default_rng(0)
    return np.vstack([blob(0, 12, 0.08, rng), blob(1, 8, 0.08, rng), blob(2, 3, 0.08, rng), blob(3, 20, 0.08, rng)])


def test_clusters_by_size_with_other(data):
    labels = clusters.cluster(data, "medium")
    assert set(labels[:12]) == {1} and set(labels[12:20]) == {2}  # numbered by size
    assert set(labels[23:]) == {0}  # the largest blob is cluster 0
    assert set(labels[20:23]) == {-1}  # three photos are too few: "Other"
    assert np.array_equal(labels, clusters.cluster(data, "medium"))  # deterministic


def test_levels_get_finer(monkeypatch):
    monkeypatch.setattr(clusters, "MIN_SIZE", 1)  # count every cluster
    rng = np.random.default_rng(1)
    X = rng.normal(size=(300, DIM))
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    counts = [len(set(clusters.cluster(X, level))) for level in ("broad", "medium", "fine")]
    assert counts[0] <= counts[1] <= counts[2]  # lower cuts of the same tree only split
    assert clusters.cluster(X[:1], "medium").tolist() == [-1]
    with pytest.raises(KeyError):
        clusters.cluster(X, "extreme")


def test_sampled_path_labels_everything(data, monkeypatch):
    monkeypatch.setattr(clusters, "MAX_DIRECT", 25)
    labels = clusters.cluster(data, "medium")
    assert len(labels) == len(data)
    assert set(labels[23:]) == {0} and set(labels[:12]) == {1}  # same clusters from a sample


def test_names():
    tags = ["boats", "other", "sheep", "sheep and lambs"]
    vecs = np.eye(4)
    near = np.array([0.1, 0.9, 0.2, 0.0])  # nearest is the catch-all "other"
    assert clusters.name(near, tags, vecs) == "sheep"
    close_pair = np.array([0, 0, 1.0, 0.99])
    assert clusters.name(close_pair, tags, vecs) == "sheep · sheep and lambs"
    assert clusters.name(near, tags, vecs, member_kinds=["an illustration or drawing"] * 3 + [None]) == "an illustration or drawing"
    assert clusters.name(near, tags, vecs, member_kinds=["a photograph"] * 4) == "sheep"  # catch-all kind ignored
    assert clusters.name(near, [], None, member_tags=["sheep", "sheep", "boats", "other"]) == "sheep"  # no model
    assert clusters.name(near, [], None) == "Similar photos"


def at(axis: int, jitter: float) -> np.ndarray:
    v = np.zeros(FakeClip.DIM, np.float32)
    v[axis] = 1.0
    v[(axis + 1) % FakeClip.DIM] = jitter
    return v / np.linalg.norm(v)


def test_similar_grouping_api(indexed, conn, monkeypatch):
    monkeypatch.setattr(clusters, "MIN_SIZE", 2)
    vectors = {  # two pairs of alike photos
        "IMG_0001.jpg": at(0, 0.05),
        "IMG_0003.png": at(0, 0.10),
        "IMG_0002.jpg": at(4, 0.05),
        "IMG_0002_edit.png": at(4, 0.10),
    }
    E, ids = load_embeddings(indexed)
    by_id = {photo(conn, n)["id"]: v for n, v in vectors.items()}
    save_embeddings(indexed, np.stack([by_id[int(i)] for i in ids]), ids)
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        res = c.get("/api/photos", params={"group": "similar", "dupes": "all"}).json()
        groups = res["groups"]
        assert [g["count"] for g in groups] == [2, 2] and all(g["label"] for g in groups)
        assert [i["group"] for i in res["items"]] == [g["key"] for g in groups for _ in range(g["count"])]  # contiguous
        first = {i["rel_path"] for i in res["items"] if i["group"] == groups[0]["key"]}
        assert first in ({"trip/IMG_0001.jpg", "trip/IMG_0003.png"}, {"trip/IMG_0002.jpg", "trip/IMG_0002_edit.png"})
        assert c.get("/api/groups", params={"group": "similar", "dupes": "all"}).json()["groups"] == groups
        # Filters narrow what is clustered: one photo per pair is too few, all go to Other.
        one = c.get("/api/photos", params={"group": "similar", "dupes": "all", "orientation": "portrait"}).json()
        assert [g["label"] for g in one["groups"]] == ["Other"]
        assert c.get("/api/photos", params={"group": "similar", "level": "extreme"}).status_code == 400
        assert c.get("/api/photos", params={"group": "similar", "level": "fine", "dupes": "all"}).status_code == 200


def test_layout(data):
    Y = clusters.layout(data)
    assert Y.shape == (len(data), 2) and Y.min() >= 0 and Y.max() <= 1.0 + 1e-9
    assert np.allclose(Y, clusters.layout(data))  # deterministic
    # alike photos land close together: a blob's spread is small next to the map
    blob_spread = np.linalg.norm(Y[23:] - Y[23:].mean(axis=0), axis=1).mean()
    assert blob_spread < 0.25
    assert clusters.layout(data[:1]).shape == (1, 2) and clusters.layout(data[:0]).shape == (0, 2)


def test_similar_map_api(indexed, conn, monkeypatch):
    monkeypatch.setattr(clusters, "MIN_SIZE", 2)
    vectors = {
        "IMG_0001.jpg": at(0, 0.05),
        "IMG_0003.png": at(0, 0.10),
        "IMG_0002.jpg": at(4, 0.05),
        "IMG_0002_edit.png": at(4, 0.10),
    }
    E, ids = load_embeddings(indexed)
    by_id = {photo(conn, n)["id"]: v for n, v in vectors.items()}
    save_embeddings(indexed, np.stack([by_id[int(i)] for i in ids]), ids)
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        m = c.get("/api/similar/map", params={"dupes": "all"}).json()
        assert len(m["points"]) == 4 and all(0 <= x <= 1 and 0 <= y <= 1 for _, x, y, _ in m["points"])
        assert sorted(g["count"] for g in m["groups"]) == [2, 2] and all(g["label"] for g in m["groups"])
        assert (indexed.embeddings_dir / f"{indexed.model.model_id}.map.npz").exists()  # cached
        where = {pid: (x, y) for pid, x, y, _ in m["points"]}
        narrowed = c.get("/api/similar/map", params={"dupes": "all", "orientation": "portrait"}).json()
        assert 0 < len(narrowed["points"]) < 4
        assert all(where[pid] == (x, y) for pid, x, y, _ in narrowed["points"])  # places stay put under filters
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:  # restart: from the cache
        again = c.get("/api/similar/map", params={"dupes": "all"}).json()
        assert {pid: (x, y) for pid, x, y, _ in again["points"]} == where


def test_similar_map_with_clusters_and_other(indexed, conn, monkeypatch):
    monkeypatch.setattr(clusters, "MIN_SIZE", 3)
    vectors = {  # three alike photos and one apart: a cluster and Other in one view
        "IMG_0001.jpg": at(0, 0.05),
        "IMG_0003.png": at(0, 0.10),
        "IMG_0002_edit.png": at(0, 0.07),
        "IMG_0002.jpg": at(4, 0.05),
    }
    E, ids = load_embeddings(indexed)
    by_id = {photo(conn, n)["id"]: v for n, v in vectors.items()}
    save_embeddings(indexed, np.stack([by_id[int(i)] for i in ids]), ids)
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        res = c.get("/api/similar/map", params={"dupes": "all"})
        assert res.status_code == 200, res.text
        assert res.json()["other"] == 1 and [g["count"] for g in res.json()["groups"]] == [3]
