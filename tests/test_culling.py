"""Flags (selections DB), stacks, sharpness, and their API."""

import os
import shutil
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageFilter

from riffle import db
from riffle.embed import load_embeddings, save_embeddings
from riffle.quality import sharpness
from riffle.server import create_app
from riffle.stacks import compute_stacks, exif_seconds
from conftest import FakeClip, _pattern, fake_index, photo


@pytest.fixture
def client(indexed, fake_clip):
    with TestClient(create_app(indexed, text_encoder=fake_clip.encode_text)) as c:
        yield c


def ids_of(conn, *names):
    return [photo(conn, n)["id"] for n in names]


def flag(client, ids, value):
    res = client.post("/api/flags", json={"ops": [{"ids": ids, "flag": value}]})
    assert res.status_code == 200, res.text
    return res.json()["previous"]


def names(client, **params):
    res = client.get("/api/photos", params={"collapse": "none", **params})
    assert res.status_code == 200, res.text
    return sorted(i["rel_path"].rsplit("/", 1)[1] for i in res.json()["items"])


# ---- flags -----------------------------------------------------------------------


def test_flags_roundtrip_filter_and_facets(client, conn):
    a, b, c = ids_of(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.png")
    assert flag(client, [a, b], "pick") == {str(a): None, str(b): None}
    assert flag(client, [b, c], "reject") == {str(b): "pick", str(c): None}

    assert names(client, flag="pick") == ["IMG_0001.jpg"]
    assert names(client, flag=["reject", "none"]) == ["IMG_0002.jpg", "IMG_0002_edit.png", "IMG_0003.png"]
    assert client.get("/api/facets").json()["flag"] == {"pick": 1, "reject": 2, "none": 1}
    items = {i["id"]: i for i in client.get("/api/photos", params={"collapse": "none"}).json()["items"]}
    assert items[a]["flag"] == "pick" and items[c]["flag"] == "reject"
    assert client.get(f"/api/photos/{a}").json()["flag"] == "pick"

    # Undo = setting the previous values back; null clears.
    assert flag(client, [c], None) == {str(c): "reject"}
    assert client.get("/api/facets").json()["flag"]["none"] == 2

    bad = client.post("/api/flags", json={"ops": [{"ids": [a], "flag": "maybe"}]})
    assert bad.status_code == 400
    assert client.post("/api/flags", content='{"ops": []}').status_code == 415
    assert client.get("/api/photos", params={"flag": "maybe"}).status_code == 400


def test_flags_survive_rebuild_and_moves(indexed, conn, archive_dir):
    """Flags live outside data/ and are keyed by content."""
    from riffle import selections

    a = photo(conn, "IMG_0003.png")["id"]
    selections.set_flags(conn, indexed.selections_path, [([a], "pick")])
    conn.close()
    assert indexed.selections_path.parent == archive_dir  # not inside data/

    shutil.rmtree(indexed.data_dir)  # derived data is disposable
    trip = archive_dir / "photos" / "trip"
    (trip / "sub").mkdir()
    os.rename(trip / "IMG_0003.png", trip / "sub" / "IMG_0003.png")
    fake_index(indexed)

    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        picked = c.get("/api/photos", params={"flag": "pick"}).json()["items"]
        assert [i["rel_path"] for i in picked] == ["trip/sub/IMG_0003.png"]


def test_select_all_ids(client, conn):
    ids = client.get("/api/ids", params={"collapse": "none"}).json()["ids"]
    listed = [i["id"] for i in client.get("/api/photos", params={"collapse": "none"}).json()["items"]]
    assert ids == listed and len(ids) == 4
    assert len(client.get("/api/ids").json()["ids"]) == 3  # duplicates collapsed


# ---- stacks and sharpness -------------------------------------------------------------


def _set_times(conn, times):
    for name, taken in times.items():
        conn.execute("UPDATE photos SET taken_at = ? WHERE id = ?", (taken, photo(conn, name)["id"]))
    conn.commit()


def _set_embeddings(cfg, conn, vectors):
    E, ids = load_embeddings(cfg)
    by_name = {n: np.array(v, np.float32) / np.linalg.norm(v) for n, v in vectors.items()}
    rows = {photo(conn, n)["id"]: by_name[n] for n in vectors}
    save_embeddings(cfg, np.stack([rows[int(i)] for i in ids]), ids)


def test_stacks_link_close_similar_shots_and_duplicates(indexed, conn):
    _set_times(conn, {
        "IMG_0001.jpg": "2024:05:01 10:00:00",
        "IMG_0002.jpg": "2024:05:01 10:00:05",
        "IMG_0003.png": "2024:05:01 10:00:20",
        "IMG_0002_edit.png": None,
    })
    _set_embeddings(indexed, conn, {
        "IMG_0001.jpg": [1, 0, 0, 0, 0, 0, 0, 0],
        "IMG_0002.jpg": [1, 0.1, 0, 0, 0, 0, 0, 0],  # similar to 0001, 5 s later
        "IMG_0003.png": [0, 0, 1, 0, 0, 0, 0, 0],  # different scene
        "IMG_0002_edit.png": [0, 0, 0, 1, 0, 0, 0, 0],  # undated, but a pHash duplicate of 0002
    })
    assert compute_stacks(conn, indexed) == (1, 3)
    a, b, c, d = ids_of(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.png", "IMG_0002_edit.png")
    stack = {r["id"]: r["stack_id"] for r in conn.execute("SELECT id, stack_id FROM photos")}
    assert stack[a] == stack[b] == stack[d] == min(a, b, d)
    assert stack[c] is None

    # Too far apart in time: only the duplicate link remains.
    _set_times(conn, {"IMG_0002.jpg": "2024:05:01 10:01:00"})
    compute_stacks(conn, indexed)
    assert conn.execute("SELECT stack_id FROM photos WHERE id = ?", (a,)).fetchone()[0] is None


def test_stack_api_review_and_collapse(indexed, conn, fake_clip):
    _set_times(conn, {"IMG_0001.jpg": "2024:05:01 10:00:00", "IMG_0002.jpg": "2024:05:01 10:00:05"})
    _set_embeddings(indexed, conn, {
        "IMG_0001.jpg": [1, 0, 0, 0, 0, 0, 0, 0],
        "IMG_0002.jpg": [1, 0.1, 0, 0, 0, 0, 0, 0],
        "IMG_0003.png": [0, 0, 1, 0, 0, 0, 0, 0],
        "IMG_0002_edit.png": [0, 0, 0, 1, 0, 0, 0, 0],
    })
    compute_stacks(conn, indexed)
    a, b, d = ids_of(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0002_edit.png")
    with TestClient(create_app(indexed, text_encoder=fake_clip.encode_text)) as c:
        stacks = c.get("/api/stacks").json()["stacks"]
        assert [(s["size"], s["unflagged"]) for s in stacks] == [(3, 3)]
        sid = stacks[0]["id"]
        members = c.get(f"/api/stacks/{sid}").json()["items"]
        assert [m["id"] for m in members] == [a, b, d]  # capture order, undated last
        assert all(m["stack_count"] == 3 and m["sharpness"] > 0 for m in members)

        # Collapsed, the stack shows one tile: the pick once there is one.
        assert len(c.get("/api/photos", params={"collapse": "stacks"}).json()["items"]) == 2
        c.post("/api/flags", json={"ops": [{"ids": [b], "flag": "pick"}, {"ids": [a, d], "flag": "reject"}]})
        tiles = c.get("/api/photos", params={"collapse": "stacks"}).json()["items"]
        assert b in [t["id"] for t in tiles] and a not in [t["id"] for t in tiles]
        assert c.get("/api/stacks", params={"unreviewed": "true"}).json()["stacks"] == []
        assert c.get("/api/photos/{}".format(a)).json()["stack"] == [a, b, d]
        assert c.get("/api/stacks/999999").status_code == 404


def test_sharpness_prefers_the_sharp_copy():
    sharp = _pattern(7, (800, 600))
    detail = Image.effect_noise((800, 600), 60).convert("RGB")
    sharp = Image.blend(sharp, detail, 0.3)
    blurred = sharp.filter(ImageFilter.GaussianBlur(3))
    assert sharpness(sharp) > 3 * sharpness(blurred)


def test_exif_seconds():
    assert exif_seconds("2024:05:01 10:00:05") - exif_seconds("2024:05:01 10:00:00") == 5
    assert exif_seconds(None) is None and exif_seconds("garbage") is None


def test_sharpness_scores_the_in_focus_region():
    """A sharp subject against a blurred background scores like the same texture
    filling the frame; the whole-image variance would be several times lower."""
    texture = Image.effect_noise((800, 600), 60).convert("RGB")
    background = texture.filter(ImageFilter.GaussianBlur(6))
    subject = background.copy()
    subject.paste(texture.crop((300, 200, 500, 400)), (300, 200))  # 200x200 sharp patch
    full, local = sharpness(texture), sharpness(subject)
    assert local == pytest.approx(full, rel=0.3)
    assert local > 5 * sharpness(background)


def test_reset_flags_all_or_filtered(client, conn):
    a, b, c = ids_of(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.png")
    flag(client, [a, b], "pick")
    flag(client, [c], "reject")
    assert (client.get("/api/tags").json()["picks"], client.get("/api/tags").json()["rejects"]) == (2, 1)

    # Only photos within the filters (here: the dated one) are unflagged.
    res = client.post("/api/flags/reset", params={"date_from": "2024-01-01"}, json={"scope": "filtered"})
    assert res.json()["previous"] == {str(a): "pick"}
    assert names(client, flag="pick") == ["IMG_0002.jpg"]

    res = client.post("/api/flags/reset", json={"scope": "all"})
    assert res.json()["previous"] == {str(b): "pick", str(c): "reject"}
    assert client.get("/api/facets").json()["flag"] == {"pick": 0, "reject": 0, "none": 4}
    assert client.post("/api/flags/reset", json={"scope": "everything"}).status_code == 400
    assert client.post("/api/flags/reset", content="{}").status_code == 415


# ---- exposure clipping and suggested keeper ----------------------------------------------


def test_clipping_counts_near_white_and_near_black_only():
    from riffle.quality import clipping

    im = Image.new("RGB", (100, 100), (128, 128, 128))
    im.paste((255, 255, 255), (0, 0, 100, 30))  # 30% blown
    im.paste((0, 0, 0), (0, 90, 100, 100))  # 10% crushed
    im.paste((255, 230, 0), (0, 30, 100, 60))  # saturated yellow: channels at 255, but not blown
    assert clipping(im) == pytest.approx((0.30, 0.10))


def test_exposure_filter_and_facet(client, conn):
    a, b = ids_of(conn, "IMG_0001.jpg", "IMG_0002.jpg")
    conn.execute("UPDATE photos SET clip_highlights = 0.2 WHERE id = ?", (a,))
    conn.execute("UPDATE photos SET clip_shadows = 0.3 WHERE id = ?", (b,))
    conn.commit()
    assert client.get("/api/facets").json()["exposure"] == {"highlights": 1, "shadows": 1, "ok": 2}
    assert names(client, exposure="highlights") == ["IMG_0001.jpg"]
    assert names(client, exposure=["highlights", "shadows"]) == ["IMG_0001.jpg", "IMG_0002.jpg"]
    assert len(names(client, exposure="ok")) == 2
    assert client.get("/api/photos", params={"exposure": "dark"}).status_code == 400


def test_keeper_scores_prefer_sharp_well_exposed():
    from riffle.quality import keeper_scores

    photos = [
        {"id": 1, "sharpness": 100, "clip_highlights": 0.0, "clip_shadows": 0.0},
        {"id": 2, "sharpness": 110, "clip_highlights": 0.12, "clip_shadows": 0.0},  # sharper but blown
        {"id": 3, "sharpness": 30, "clip_highlights": 0.0, "clip_shadows": 0.0},  # soft
    ]
    best, scores = keeper_scores(photos)
    assert best == 1 and scores[2]["exposure"] == 0 and scores[3]["sharpness"] == pytest.approx(30 / 110)
    # The CLIP quality score can tip a close call.
    best, scores = keeper_scores(photos[:1] + [dict(photos[0], id=4, sharpness=98)], {1: 0.2, 4: 0.9})
    assert best == 4 and scores[4]["quality"] == 1 and scores[1]["quality"] == 0
    assert keeper_scores([]) == (None, {})


def test_suggest_endpoint(client, conn):
    a, b, c = ids_of(conn, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.png")
    conn.execute("UPDATE photos SET sharpness = 50, clip_highlights = 0, clip_shadows = 0 WHERE id IN (?, ?, ?)", (a, b, c))
    conn.execute("UPDATE photos SET sharpness = 500 WHERE id = ?", (b,))
    conn.commit()
    res = client.get("/api/suggest", params={"ids": f"{a},{b},{c}"}).json()
    assert res["suggested"] == b
    assert set(res["scores"][str(b)]) == {"sharpness", "exposure", "quality", "total"}
    assert client.get("/api/suggest", params={"ids": "x"}).status_code == 400
