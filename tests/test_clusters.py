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


def test_a_too_large_cluster_splits(monkeypatch):
    rng = np.random.default_rng(3)
    def sub(axis, n):
        X = np.zeros((n, DIM))
        X[:, 0], X[:, axis] = 1.0, 0.55  # two subjects sharing one strong direction ("illustration")
        X += 0.02 * rng.normal(size=(n, DIM))
        return X / np.linalg.norm(X, axis=1, keepdims=True)
    X = np.vstack([sub(5, 20), sub(6, 20)])
    monkeypatch.setattr(clusters, "MIN_CAP", 25)
    monkeypatch.setattr(clusters, "MAX_SHARE", {**clusters.MAX_SHARE, "medium": 1.0})
    assert len(set(clusters.cluster(X, "medium"))) == 1  # no cap: one cluster of 40
    monkeypatch.setattr(clusters, "MAX_SHARE", {**clusters.MAX_SHARE, "medium": 0.5})
    labels = clusters.cluster(X, "medium")  # capped at 25: split by subject
    assert set(labels[:20]) != set(labels[20:]) and len(set(labels[:20])) == 1 and len(set(labels[20:])) == 1
    assert (labels >= 0).all()  # nothing lost to Other by the split


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


def namer(phrases, setting=(), baseline=None):
    vecs = np.eye(len(phrases))
    return clusters.Namer(
        phrases=list(phrases),
        vecs=vecs,
        baseline=np.zeros(len(phrases)) if baseline is None else np.array(baseline, float),
        setting=np.array([p in setting for p in phrases]),
    )


def test_names_pick_the_distinctive_phrase():
    n = namer(["sheep", "a meadow", "a blue sky"], baseline=[0.1, 0.6, 0.0])
    # "a meadow" is nearest, but every photo of this library is: "sheep" is what stands out
    assert clusters.names([np.array([0.5, 0.8, 0.0])], n, [[]]) == ["sheep"]
    assert clusters.names([np.array([0.5, 0.8, 0.0])], namer(["sheep", "a meadow", "a blue sky"]), [[]]) == ["a meadow"]


def test_a_thing_close_behind_a_setting_comes_first():
    n = namer(["birds in flight", "boats", "a blue sky"], setting=["a blue sky"])
    close = np.array([0.80, 0.0, 0.82])  # the sky wins, the birds are close behind
    assert clusters.names([close], n, [[]]) == ["birds in flight · a blue sky"]
    far = np.array([0.40, 0.0, 0.82])  # nothing close: the setting alone
    assert clusters.names([far], n, [[]]) == ["a blue sky"]


def test_custom_tags_name_first():
    n = namer(["cats", "dogs"])
    members = [["Our cat"], ["Our cat"], ["Our cat", "Garden"], []]
    illus = ["an illustration or drawing"] * 4
    assert clusters.names([np.array([1.0, 0])], n, [illus], member_custom=[members]) == ["Our cat"]  # 3 of 4
    few = [["Our cat"], [], [], []]
    assert clusters.names([np.array([1.0, 0])], n, [[None] * 4], member_custom=[few]) == ["cats"]  # 1 of 4: not enough


def test_fixed_tags_name_clusters():
    n = namer(["an anime illustration", "swimmers", "figurines", "boats"])
    illus = ["an illustration or drawing"] * 4
    photos = ["a photograph"] * 12  # illustrations don't fill the view, so the kind prefixes their names
    swim = np.array([0.9, 0.5, 0.0, 0.0])
    figs = np.array([0.9, 0.0, 0.5, 0.0])
    boats = np.array([0.0, 0.0, 0.0, 1.0])
    # Two illustration clusters, compared with each other: "1girl" and "long hair" are on most illustrations, so
    # they name neither; the character and "water" set the first apart; the second has
    # no distinctive fixed tag and keeps its phrase. Photos have no fixed tags.
    nia = [["1girl", "nia_(xenoblade)", "water", "long hair"]] * 3 + [["1girl", "long hair"]]
    other = [["1girl", "long hair"]] * 4
    fixed = [nia, other, [[]] * 12]
    got = clusters.names([swim, figs, boats], n, [illus, illus, photos], member_fixed=fixed, characters=frozenset({"nia (xenoblade)"}))
    assert got == ["Illustrations: nia_(xenoblade) · water", "Illustrations: figurines", "boats"]
    # A second tag within the first ("choker" in "black choker") says nothing more.
    chokers = [[["black choker", "choker", "tail"]] * 4, other, [[]] * 12]
    got = clusters.names([swim, figs, boats], n, [illus, illus, photos], member_fixed=chokers)
    assert got[0] in ("Illustrations: black choker · tail", "Illustrations: tail · black choker")
    # Too few photos with fixed tags at all (1 of 4): the phrase names it.
    sparse = [[["water"], [], [], []], [[]] * 4, [[]] * 12]
    assert clusters.names([swim, figs, boats], n, [illus, illus, photos], member_fixed=sparse)[0] == "Illustrations: swimmers"
    # Learned (custom) tags still come first.
    custom = [[["Nia"]] * 4, [[]] * 4, [[]] * 12]
    assert clusters.names([swim, figs, boats], n, [illus, illus, photos], member_custom=custom, member_fixed=fixed)[0] == "Nia"


def test_several_clusters_of_a_kind_are_named_by_content():
    n = namer(["an anime illustration", "swimmers", "figurines", "boats"])
    illus = ["an illustration or drawing"] * 4
    photos = ["a photograph"] * 12
    swim = np.array([0.9, 0.5, 0.0, 0.0])  # both illustration clusters are very "anime illustration"...
    figs = np.array([0.9, 0.0, 0.5, 0.0])  # ...and differ in what they show
    boats = np.array([0.0, 0.0, 0.0, 1.0])
    got = clusters.names([swim, figs, boats], n, [illus, illus, photos])
    assert got == ["Illustrations: swimmers", "Illustrations: figurines", "boats"]


def test_kind_first_duplicates_and_fallbacks():
    n = namer(["anime figures", "sheep", "lambs"])
    illus = ["an illustration or drawing"] * 3 + [None]
    photos = ["a photograph"] * 6
    # In a mixed view, a cluster of illustrations is named by its kind...
    assert clusters.names([np.array([1.0, 0, 0]), np.array([0, 1.0, 0])], n, [illus, photos])[0] == "Illustrations"
    # ...but not when illustrations fill the view: then the kind says nothing about it.
    assert clusters.names([np.array([1.0, 0, 0])], n, [illus]) == ["anime figures"]
    assert clusters.names([np.array([1.0, 0, 0])], n, [["a photograph"] * 4]) == ["anime figures"]  # catch-all kind
    two = clusters.names([np.array([0, 1.0, 0.5]), np.array([0, 1.0, 0.2])], n, [[], []])
    assert two == ["sheep", "sheep 2"]  # told apart; "lambs" fits the second too poorly to name it
    close = clusters.names([np.array([0, 1.0, 0.96]), np.array([0, 1.0, 0.98])], n, [[], []])
    assert close == ["sheep", "sheep · lambs"]  # a well-fitting phrase tells them apart
    assert clusters.names([np.zeros(3) + 1], None, [[]], [["sheep", "sheep", "boats", "other"]]) == ["sheep"]
    assert clusters.names([np.zeros(3) + 1], None, [[]], [[]]) == ["Similar photos"]


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


def test_a_custom_tag_names_its_cluster(indexed, conn, monkeypatch):
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
    pair = [photo(conn, "IMG_0001.jpg")["id"], photo(conn, "IMG_0003.png")["id"]]
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        before = c.get("/api/groups", params={"group": "similar", "dupes": "all"}).json()["groups"]
        assert "Lanterns" not in [g["label"] for g in before]
        c.post("/api/custom-tags", json={"name": "Lanterns", "photo_ids": pair, "strictness": "strict"})
        after = c.get("/api/groups", params={"group": "similar", "dupes": "all"}).json()["groups"]  # not the cached names
        assert "Lanterns" in [g["label"] for g in after]


def test_fixed_tags_name_a_cluster_in_the_api(indexed, conn, monkeypatch):
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
    pair = [photo(conn, "IMG_0001.jpg")["id"], photo(conn, "IMG_0003.png")["id"]]
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        before = c.get("/api/groups", params={"group": "similar", "dupes": "all"}).json()["groups"]
        assert "Midsummer" not in [g["label"] for g in before]
        c.post("/api/captions", json={"items": [{"id": i, "tags": ["Midsummer"]} for i in pair]})
        after = c.get("/api/groups", params={"group": "similar", "dupes": "all"}).json()["groups"]  # not the cached names
        assert "Midsummer" in [g["label"] for g in after]


def test_clusters_are_cached_on_disk(indexed, conn, monkeypatch):
    monkeypatch.setattr(clusters, "MIN_SIZE", 2)
    vectors = {"IMG_0001.jpg": at(0, 0.05), "IMG_0003.png": at(0, 0.10), "IMG_0002.jpg": at(4, 0.05), "IMG_0002_edit.png": at(4, 0.10)}
    E, ids = load_embeddings(indexed)
    by_id = {photo(conn, n)["id"]: v for n, v in vectors.items()}
    save_embeddings(indexed, np.stack([by_id[int(i)] for i in ids]), ids)
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        first = c.get("/api/groups", params={"group": "similar", "dupes": "all"}).json()["groups"]
    assert list((indexed.embeddings_dir / "clusters").glob("*.npy"))
    # A restart (a new app, nothing in memory) reads them instead of clustering again.
    monkeypatch.setattr(clusters, "cluster", lambda *a, **k: (_ for _ in ()).throw(AssertionError("clustered again")))
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        again = c.get("/api/groups", params={"group": "similar", "dupes": "all"}).json()["groups"]
    assert [(g["key"], g["count"]) for g in again] == [(g["key"], g["count"]) for g in first]
