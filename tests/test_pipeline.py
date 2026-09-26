import numpy as np
from PIL import Image

from riffle.dupes import group_pairs, hamming
from riffle.embed import embed_photos, load_embeddings
from riffle.raws import match_raws
from riffle.scan import scan
from riffle.tags import Label, assign, label_similarities, load_vocabulary, tag_photos
from conftest import photo


def test_raw_matching(cfg, conn):
    result = scan(conn, cfg)
    matched, unmatched = match_raws(conn, cfg, result.raws)
    assert (matched, unmatched) == (1, 1)
    linked = conn.execute(
        "SELECT r.rel_path, p.rel_path AS img FROM raws r JOIN photos p ON p.id = r.photo_id"
    ).fetchall()
    assert [(r["rel_path"], r["img"]) for r in linked] == [
        ("trip/RAW/IMG_0001.CR3", "trip/IMG_0001.jpg")
    ]


def test_thumbnails_are_upright_and_sized(indexed, conn):
    p = photo(conn, "IMG_0001.jpg")
    with Image.open(indexed.thumbs_dir / f"{p['id']}.jpg") as t:
        assert t.size == (213, 320)  # portrait after EXIF orientation 6
    q = photo(conn, "IMG_0002_edit.png")
    with Image.open(indexed.previews_dir / f"{q['id']}.jpg") as pv:
        assert max(pv.size) == 1280  # never upscaled
    assert photo(conn, "broken.jpg")["status"] == "error"


def test_embeddings_are_normalised_and_incremental(indexed, conn, fake_clip, archive_dir):
    E, ids = load_embeddings(indexed)
    assert E.shape == (4, fake_clip.DIM)
    assert np.allclose(np.linalg.norm(E, axis=1), 1)
    assert list(ids) == sorted(ids)
    assert embed_photos(conn, indexed, clip=fake_clip) == 0

    (archive_dir / "photos" / "trip" / "IMG_0003.png").unlink()
    scan(conn, indexed)
    assert embed_photos(conn, indexed, clip=fake_clip) == 0
    _, ids2 = load_embeddings(indexed)
    assert len(ids2) == 3


def test_tag_assignment_thresholds():
    E = np.array([[1.0, 0.0], [0.0, 1.0], [0.7071, 0.7071]], dtype=np.float32)
    T = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    out = assign(E @ T.T, scale=100, min_prob=0.3, max_tags=2)
    assert [j for j, _, _ in out[0]] == [0]
    assert [j for j, _, _ in out[1]] == [1]
    assert sorted(j for j, _, _ in out[2]) == [0, 1]  # a tie keeps both


def test_tags_written_per_family(indexed, conn, fake_clip):
    counts = dict(
        conn.execute(
            """SELECT t.family, COUNT(*) FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
               GROUP BY t.family"""
        ).fetchall()
    )
    assert counts == {"scene": 4, "subject": 8}

    # Removing a label from the vocabulary drops it on re-tag.
    indexed.vocabulary_path.write_text(
        'templates: ["{}"]\nsubject: [red things]\nscene: [indoors, outdoors]\n'
    )
    E, ids = load_embeddings(indexed)
    tag_photos(conn, indexed, E, ids, fake_clip.encode_text)
    names = {r[0] for r in conn.execute("SELECT name FROM tags")}
    assert names == {"red things", "indoors", "outdoors"}


def test_hamming_and_union_find():
    h = np.array([0b0, 0b1, 0b11, 0xFF], dtype=np.uint64)
    d = hamming(h, h)
    assert d[0, 1] == 1 and d[0, 3] == 8 and d[2, 3] == 6
    assert group_pairs(5, np.array([[0, 1], [3, 4], [1, 2]])) == [0, 0, 0, 3, 3]


def test_duplicates_grouped(indexed, conn):
    a, b = photo(conn, "IMG_0002.jpg"), photo(conn, "IMG_0002_edit.png")
    assert a["dupe_group"] is not None
    assert a["dupe_group"] == b["dupe_group"] == min(a["id"], b["id"])
    assert photo(conn, "IMG_0003.png")["dupe_group"] is None


def test_vocabulary_alternative_phrases(tmp_path):
    path = tmp_path / "v.yaml"
    path.write_text(
        'subject:\n  - cats: [a cat, a kitten]\n  - dogs: a dog\n  - boats\n'
    )
    v = load_vocabulary(path)
    assert [(l.name, l.phrases) for l in v.families["subject"]] == [
        ("cats", ["cats", "a cat", "a kitten"]),
        ("dogs", ["dogs", "a dog"]),
        ("boats", ["boats"]),
    ]


def test_label_similarity_is_max_over_phrases():
    vecs = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}
    encode = lambda texts: np.array([vecs[t] for t in texts], dtype=np.float32)
    E = np.array([[0.6, 0.8, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)
    labels = [Label("xy", ["x", "y"]), Label("z", ["z"])]
    sims = label_similarities(E, labels, ["{}"], encode)
    assert np.allclose(sims, [[0.8, 0.0], [0.0, 1.0]])
