"""Colour and light measures (colors.py) and their use in Curate."""

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from riffle.colors import HUES, color_stats, hue_affinity
from riffle.server import create_app
from conftest import FakeClip, photo


def solid(rgb):
    return Image.new("RGB", (64, 48), rgb)


def test_brightness_contrast_and_colorfulness():
    white, black, grey = (color_stats(solid(c)) for c in ("white", "black", (128, 128, 128)))
    assert white[0] > 0.99 and black[0] < 0.01 and 0.45 < grey[0] < 0.55
    assert white[1] < 0.01  # flat
    checker = Image.fromarray((np.indices((48, 64)).sum(axis=0) % 2 * 255).astype(np.uint8)).convert("RGB")
    assert color_stats(checker)[1] > 0.45  # half black, half white: maximal contrast
    red = color_stats(solid((220, 30, 30)))
    assert red[2] > grey[2] + 0.2  # colourful vs grey


def test_hues():
    def affinity(rgb, name):
        return hue_affinity(color_stats(solid(rgb))[3], HUES[name][0])

    assert affinity((220, 30, 30), "red") > 0.3
    assert affinity((220, 30, 30), "blue") == 0
    assert affinity((40, 90, 220), "blue") > 0.3
    assert affinity((240, 150, 40), "orange") > affinity((240, 150, 40), "red")
    assert affinity((128, 128, 128), "red") == 0  # grey carries no hue
    assert hue_affinity(None, 0) == 0


def test_indexing_stores_them(indexed, conn):
    row = photo(conn, "IMG_0003.png")
    assert row["brightness"] is not None and row["contrast"] is not None and row["colorfulness"] is not None
    assert len(row["hues"]) == 24 * 4  # float32 per bin
    # …and the layout fingerprint for Discover, the same way.
    lay = conn.execute("SELECT layout FROM photos WHERE status = 'ok' AND layout IS NOT NULL").fetchone()
    assert lay is not None and len(lay[0]) == 12 * 12 * 4


def test_curate_leans_towards_a_look(indexed, conn):
    ids = [r["id"] for r in conn.execute("SELECT id FROM photos WHERE status = 'ok' ORDER BY id")]
    bright = ids[0]
    conn.executemany("UPDATE photos SET brightness = ? WHERE id = ?", [(0.9 if i == bright else 0.1, i) for i in ids])
    conn.execute("UPDATE photos SET brightness = NULL WHERE id = ?", (ids[-1],))  # not analysed yet
    conn.commit()
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        hues = c.get("/api/hues").json()
        assert [h["name"] for h in hues] == list(HUES) and all(h["color"].startswith("#") for h in hues)
        body = {"n": 2, "variety": 0, "time_spread": 0, "place_spread": 0}
        d = c.post("/api/curate", params={"dupes": "all"}, json=body | {"look": {"brightness": 1}}).json()
        assert bright in [i["id"] for i in d["items"]]
        assert "look.brightness" in d["used"]["styles"] and d["used"]["colors_missing"] == 1
        assert "very bright" in next(i["reason"] for i in d["items"] if i["id"] == bright)
        dark = c.post("/api/curate", params={"dupes": "all"}, json=body | {"look": {"brightness": -1}}).json()
        assert bright not in [i["id"] for i in dark["items"]]
        plain = c.post("/api/curate", json=body).json()
        ignored = c.post("/api/curate", json=body | {"look": {"brightness": 0, "hue": "nonsense"}}).json()
        assert [i["id"] for i in ignored["items"]] == [i["id"] for i in plain["items"]]
