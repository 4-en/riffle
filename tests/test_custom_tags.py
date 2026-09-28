"""Tags taught by example photos (custom_tags.py, selections v3, the API)."""

import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from riffle import custom_tags, selections
from riffle.embed import load_embeddings, save_embeddings
from riffle.server import create_app
from conftest import FakeClip, fake_index, photo

DIM = FakeClip.DIM


def at(cos: float, axis: int) -> np.ndarray:
    """A unit vector with this cosine similarity to axis 0."""
    v = np.zeros(DIM, np.float32)
    v[0], v[axis] = cos, np.sqrt(1 - cos * cos)
    return v


# Similarity to the example (IMG_0001), with the default stack threshold 0.92:
# strict 0.87, normal 0.84, loose 0.80.
VECTORS = {
    "IMG_0001.jpg": at(1.0, 1),
    "IMG_0002.jpg": at(0.855, 2),  # normal and loose
    "IMG_0002_edit.png": at(0.815, 3),  # loose only
    "IMG_0003.png": at(0.0, 4),  # never
}


def set_embeddings(cfg, conn):
    E, ids = load_embeddings(cfg)
    rows = {photo(conn, n)["id"]: v for n, v in VECTORS.items()}
    save_embeddings(cfg, np.stack([rows[int(i)] for i in ids]), ids)


@pytest.fixture
def client(indexed, conn):
    set_embeddings(indexed, conn)
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        yield c


def ids(conn, *names):
    return [photo(conn, n)["id"] for n in names]


def listed(client, **params):
    return sorted(i["id"] for i in client.get("/api/photos", params={"dupes": "all", **params}).json()["items"])


def create(client, name, photo_ids, strictness="normal"):
    res = client.post("/api/custom-tags", json={"name": name, "photo_ids": photo_ids, "strictness": strictness})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_members_follow_strictness(client, conn):
    ex, near, loose, far = ids(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0002_edit.png", "IMG_0003.png")
    preview = client.post("/api/custom-tags/preview", json={"photo_ids": [ex], "strictness": "normal"}).json()
    assert preview["counts"] == {"strict": 1, "normal": 2, "loose": 3}
    assert [i["id"] for i in preview["edge"]] == [near]  # the members nearest the edge

    tag = create(client, "Lanterns", [ex])
    assert listed(client, ctags=tag) == sorted([ex, near])
    client.post(f"/api/custom-tags/{tag}", json={"strictness": "loose"})
    assert listed(client, ctags=tag) == sorted([ex, near, loose])
    client.post(f"/api/custom-tags/{tag}", json={"strictness": "strict"})
    assert listed(client, ctags=tag) == [ex]  # the example always belongs
    assert listed(client, exclude_ctags=tag) == sorted([near, loose, far])


def test_best_match_among_several_examples(client, conn):
    ex, near, loose, far = ids(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0002_edit.png", "IMG_0003.png")
    tag = create(client, "Both", [ex, far])  # two unrelated examples: each covers its own neighbours
    assert listed(client, ctags=tag) == sorted([ex, near, far])


def test_negatives_carve_out_their_surroundings():
    e, near_neg, other_way, neg = at(1.0, 1), at(0.86, 2), at(0.86, 3), at(0.8, 2)
    E = np.stack([e, near_neg, other_way, neg])
    E /= np.linalg.norm(E, axis=1, keepdims=True)
    # (One example: the rule. Normal is 0.08 below the stack threshold, 0.92 here.)
    keep = custom_tags.tag_scores(E, [0], 0.92).members("normal")
    assert keep.tolist() == [True, True, True, False]  # without negatives: as before
    keep = custom_tags.tag_scores(E, [0], 0.92, [3]).members("normal")
    # Row 1 is more like the negative than like the example; row 2 lies the other way.
    assert keep.tolist() == [True, False, True, False]
    assert custom_tags.tag_scores(E, [0], 0.92, [0]).members("normal")[0]  # an example always belongs


def test_negatives_in_the_api(client, conn):
    ex, near, loose, far = ids(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0002_edit.png", "IMG_0003.png")
    preview = client.post("/api/custom-tags/preview", json={"photo_ids": [ex], "strictness": "normal", "negatives": [near]}).json()
    assert preview["counts"]["normal"] == 1 and preview["left_out"] == 1 and preview["edge"] == []
    assert preview["counts"]["loose"] == 1  # the loose one is its stack mate (the edit): a negative covers its stack

    tag = client.post("/api/custom-tags", json={"name": "Lanterns", "photo_ids": [ex], "negatives": [near]}).json()["id"]
    assert listed(client, ctags=tag) == [ex]
    assert client.get("/api/tags").json()["custom"][0]["negatives"] == [near]
    client.post(f"/api/custom-tags/{tag}", json={"remove_negatives": [near]})
    assert listed(client, ctags=tag) == sorted([ex, near])
    client.post(f"/api/custom-tags/{tag}", json={"add_negatives": [near]})
    client.post(f"/api/custom-tags/{tag}", json={"add": [near]})  # an example now: no longer a negative
    t = client.get("/api/tags").json()["custom"][0]
    assert sorted(t["examples"]) == sorted([ex, near]) and t["negatives"] == []

    # A negative that is (almost) one of the examples contradicts it.
    assert client.post("/api/custom-tags", json={"name": "Odd", "photo_ids": [far], "negatives": [far]}).status_code == 400
    assert client.post(f"/api/custom-tags/{tag}", json={"add_negatives": [ex]}).status_code == 400
    assert client.delete(f"/api/custom-tags/{tag}").status_code == 200


def test_editing_and_errors(client, conn):
    ex, near, loose, far = ids(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0002_edit.png", "IMG_0003.png")
    tag = create(client, "Things", [ex])
    assert client.post("/api/custom-tags", json={"name": "things", "photo_ids": [far]}).status_code == 409  # names ignore case
    assert client.post("/api/custom-tags", json={"name": "  ", "photo_ids": [far]}).status_code == 400
    assert client.post("/api/custom-tags", json={"name": "Nothing", "photo_ids": []}).status_code == 400
    assert client.post("/api/custom-tags", json={"name": "Odd", "photo_ids": [far], "strictness": "extreme"}).status_code == 400

    client.post(f"/api/custom-tags/{tag}", json={"name": "Renamed", "add": [far]})
    custom = client.get("/api/tags").json()["custom"]
    assert [(t["name"], sorted(t["examples"])) for t in custom] == [("Renamed", sorted([ex, far]))]
    client.post(f"/api/custom-tags/{tag}", json={"remove": [ex]})
    assert listed(client, ctags=tag) == [far]
    assert client.post("/api/custom-tags/999", json={"name": "x"}).status_code == 404

    assert client.delete(f"/api/custom-tags/{tag}").status_code == 200
    assert client.get("/api/tags").json()["custom"] == []
    assert client.delete(f"/api/custom-tags/{tag}").status_code == 404
    assert listed(client, ctags=tag) == []  # an unknown tag matches nothing


def test_counts_detail_and_combining_filters(client, conn):
    ex, near, loose, far = ids(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0002_edit.png", "IMG_0003.png")
    tag = create(client, "Lanterns", [ex])
    other = create(client, "Other", [far])
    counts = {t["name"]: t["count"] for t in client.get("/api/tags").json()["custom"]}
    assert counts == {"Lanterns": 2, "Other": 1}
    client.post("/api/flags", json={"ops": [{"ids": [near], "flag": "pick"}]})
    within = {t["name"]: t["count"] for t in client.get("/api/tags", params={"flag": "pick"}).json()["custom"]}
    assert within == {"Lanterns": 1, "Other": 0}  # 0-count tags stay listed
    assert listed(client, ctags=tag, flag="pick") == [near]
    assert listed(client, ctags=f"{tag},{other}") == []  # AND
    assert client.get("/api/search/text", params={"q": "x", "ctags": tag, "dupes": "all"}).json()["total"] == 2
    assert [t["name"] for t in client.get(f"/api/photos/{near}").json()["custom_tags"]] == ["Lanterns"]
    assert client.get(f"/api/photos/{loose}").json()["custom_tags"] == []
    assert client.get("/api/photos", params={"ctags": "x"}).status_code == 400


def test_tags_survive_reindexing_and_lost_examples(indexed, conn, archive_dir):
    set_embeddings(indexed, conn)
    ex = photo(conn, "IMG_0003.png")["id"]
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        tag = create(c, "Kept", [ex])
    fake_index(indexed)  # re-index: new embeddings, same content hashes
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        assert [t["examples"] for t in c.get("/api/tags").json()["custom"]] == [[ex]]
        assert ex in listed(c, ctags=tag)
    (archive_dir / "photos" / "trip" / "IMG_0003.png").unlink()  # its only example is gone
    fake_index(indexed)
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        t = c.get("/api/tags").json()["custom"][0]
        assert t["examples"] == [] and t["count"] == 0
        assert listed(c, ctags=tag) == []


def test_selections_migrate_from_v2(tmp_path):
    path = tmp_path / "selections.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        """CREATE TABLE flags (sha256 TEXT PRIMARY KEY, flag TEXT NOT NULL, source TEXT, rel_path TEXT, updated_at REAL NOT NULL);
           CREATE TABLE exported (sha256 TEXT PRIMARY KEY, first_at REAL NOT NULL, last_at REAL NOT NULL,
             times INTEGER NOT NULL DEFAULT 1, last_folder TEXT, source TEXT, rel_path TEXT);
           INSERT INTO flags VALUES ('abc', 'pick', 's', 'a.jpg', 1);
           PRAGMA user_version = 2;"""
    )
    old.commit()
    old.close()
    conn = selections.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 3
    assert conn.execute("SELECT flag FROM flags").fetchone()[0] == "pick"
    assert conn.execute("SELECT COUNT(*) FROM custom_tags").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM custom_tag_negatives").fetchone()[0] == 0  # v7


def test_from_four_examples_a_classifier_decides():
    """From four examples a classifier scores the photos (the rule below that); the cut
    is relative to how it scores its own examples, the presets are points on that
    scale, marked photos never belong and examples always do. (That it orders photos
    better than the rule was measured on a real library: custom_tags.py.)"""
    rng = np.random.default_rng(0)
    dim = 32
    trait = np.eye(dim)[1]

    def photos(n, v, noise=0.15):
        X = v + noise * rng.normal(size=(n, dim))
        return X / np.linalg.norm(X, axis=1, keepdims=True)

    members = photos(40, np.eye(dim)[2] + 0.6 * trait)
    others = photos(300, np.zeros(dim), noise=1.0)
    E = np.concatenate([members, others]).astype(np.float32)
    examples = list(range(6))
    assert not custom_tags.tag_scores(E, examples[:3], 0.92).learned  # three: still the rule
    t = custom_tags.tag_scores(E, examples, 0.92)
    assert t.learned
    assert t.threshold("strict") > t.threshold("normal") > t.threshold("loose") > 0
    assert t.threshold(0.75) == t.threshold("normal") and t.threshold(1.0) == t.threshold("strict")
    assert t.threshold(99) == t.threshold(custom_tags.CUT_RANGE[1])  # clamped
    keep = t.members("normal")
    assert keep[:40].mean() > 0.8 and keep[40:].mean() < 0.05
    loose, strict = t.members(0.3).sum(), t.members(1.2).sum()
    assert loose >= keep.sum() >= strict  # the slider: more or fewer
    marked = custom_tags.tag_scores(E, examples, 0.92, [10, 11]).members("normal")
    assert not marked[10] and not marked[11] and marked[examples].all()


def test_the_cut_is_saved_per_tag(client, conn):
    ex, near, loose, far = ids(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0002_edit.png", "IMG_0003.png")
    # (One example: the rule, on the same scale: 1.0 Strict, 0.75 Normal, 0.5 Loose.)
    preview = client.post("/api/custom-tags/preview", json={"photo_ids": [ex], "cut": 0.5}).json()
    assert preview["count"] == 3 and preview["cut"] == 0.5 and preview["counts"] == {"strict": 1, "normal": 2, "loose": 3}
    assert client.post("/api/custom-tags/preview", json={"photo_ids": [ex], "cut": 9}).json()["cut"] == 1.3  # clamped
    tag = client.post("/api/custom-tags", json={"name": "Lanterns", "photo_ids": [ex], "cut": 0.5}).json()["id"]
    assert listed(client, ctags=tag) == sorted([ex, near, loose])
    t = client.get("/api/tags").json()["custom"][0]
    assert t["cut"] == 0.5 and t["strictness"] == "loose"  # (the nearest preset, for older clients)
    client.post(f"/api/custom-tags/{tag}", json={"cut": 1.0})
    assert listed(client, ctags=tag) == [ex]
    client.post(f"/api/custom-tags/{tag}", json={"strictness": "normal"})  # a preset clears the cut
    assert client.get("/api/tags").json()["custom"][0]["cut"] == 0.75
    assert listed(client, ctags=tag) == sorted([ex, near])
