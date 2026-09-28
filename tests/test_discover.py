"""Discover (discover.py and /api/discover): lenses, exclusions, drift, the endpoint."""

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from riffle import discover
from riffle.server import create_app
from conftest import FakeClip, photo


def unit(*xs):
    v = np.array(xs, dtype=float)
    return v / np.linalg.norm(v)


def hues(**at):
    """A hue histogram with mass at named bins (0 red, 4 yellow-ish, 8 green, 14 blue)."""
    h = np.zeros(24, np.float32)
    for b, m in at.items():
        h[int(b[1:])] = m
    return h


def make_pool():
    # Rows: 0 centre (red sheep), 1 near-copy (same stack), 2 another sheep at night,
    # 3 red but not a sheep, 4 blue harbour, 5 a sheep on another day, 6 same layout.
    E = np.array([unit(1, 0, 0, 0), unit(1, 0.05, 0, 0), unit(0.8, 0.6, 0, 0), unit(0.1, 0, 1, 0),
                  unit(0, 0, 0.2, 1), unit(0.9, 0.2, 0.3, 0), unit(0.2, 0.1, 0.3, 0.9)])
    things = np.array([[1, 0], [1, 0], [1, 0], [0, 1], [0, 1], [1, 0], [0, 1]], float)
    settings = np.array([[1, 0], [1, 0], [0, 1], [1, 0], [0, 1], [0, 1], [0, 1]], float)
    layout = np.zeros((7, discover.GRID * discover.GRID), np.float32)
    rng = np.random.default_rng(0)
    base = rng.normal(size=discover.GRID * discover.GRID)
    base = (base - base.mean()) / base.std()
    layout[0] = layout[6] = base
    for k in (1, 2, 3, 4, 5):
        layout[k] = rng.normal(size=discover.GRID * discover.GRID)
    pool = discover.Pool(
        ids=np.array([10, 11, 12, 13, 14, 15, 16]),
        E=E,
        group=np.array([1, 1, -12, -13, -14, -15, -16]),
        day=["2024-05-01", "2024-05-01", "2024-05-01", "2024-06-01", "2024-05-01", "2025-01-01", ""],
        taken=np.array([0.0, 60, 3600, 86400 * 31, 7200, 86400 * 250, np.nan]),
        brightness=np.array([0.6, 0.6, 0.1, 0.55, 0.5, 0.6, 0.5]),
        contrast=np.array([0.2] * 7),
        colorfulness=np.array([0.5, 0.5, 0.1, 0.5, 0.5, 0.5, 0.3]),
        hues=np.stack([hues(b0=0.3), hues(b0=0.3), hues(b0=0.02), hues(b0=0.28), hues(b12=0.3), hues(b8=0.2), hues(b8=0.05)]),
        layout=layout,
        things=things,
        settings=settings,
        thing_names=["sheep", "harbour"],
        setting_names=["golden hour", "night"],
    )
    pool.kind = [""] * 7
    return pool


def lens_ids(branches, lens):
    return [p for b in branches if b["lens"] == lens for p, *_ in b["photos"]]


def first(pool, lens, **kw):
    """The branch of one lens, asked for first (a photo shows on one branch only)."""
    return next(b for b in discover.discover(pool, 10, prefs={lens: 1}, **kw) if b["lens"] == lens)


def test_lenses_find_their_kind_of_relation():
    pool = make_pool()
    ids = lambda lens: [p for p, *_ in first(pool, lens)["photos"]]  # noqa: E731
    assert ids("colour")[0] == 13  # red, but not a sheep: the colour echo, not the nearest photo
    assert ids("complement")[0] == 14  # red ↔ blue-green (the opposite side of the wheel)
    assert ids("shape")[0] == 16  # the same layout, different content
    assert ids("light")[0] == 13  # the centre's light (golden hour), another subject
    opposite = first(pool, "opposite")["photos"]
    assert opposite[0][0] == 12 and "darker" in opposite[0][2]  # a sheep, much darker
    moment = first(pool, "moment")["photos"]
    assert [p for p, *_ in moment] == [14, 12] and "later" in moment[0][2]  # the same day, the least alike first
    subject = first(pool, "subject")
    assert subject["photos"][0][0] in (12, 15) and "sheep" in subject["reason"]


def test_accent_lenses():
    pool = make_pool()
    pool.hues[0] = hues(b14=0.3)  # the centre: mostly blue, with a red accent (a balloon in the sky)
    red = "[[2.0, 0.8, 0.02]]"
    pool.accents = discover.accent_matrix([red, red, None, None, red, None, red])
    echo = first(pool, "accent")
    assert echo["photos"][0][0] == 14 and echo["reason"] == "a red accent"  # the same accent, least alike first
    assert 11 not in lens_ids([echo], "accent")  # (its stack mate)
    grows = first(pool, "accent_grows")
    assert grows["photos"][0][0] == 13 and "red" in grows["reason"]  # red all over
    pool.hues[0] = hues(b0=0.3)  # a mostly red centre: red is its main colour, not an accent
    assert "accent_grows" not in {b["lens"] for b in discover.discover(pool, 10)}
    pool.accents[0] = 0  # no accent: neither lens
    assert not {"accent", "accent_grows"} & {b["lens"] for b in discover.discover(pool, 10)}


def test_exclusions_and_no_photo_twice():
    pool = make_pool()
    branches = discover.discover(pool, 10, trail=[13])
    shown = [p for b in branches for p, *_ in b["photos"]]
    assert 11 not in shown  # the centre's stack mate
    assert 13 not in shown  # on the trail
    assert len(shown) == len(set(shown))  # each photo on one branch only
    allowed = np.array([k != 4 for k in range(7)])
    assert 14 not in [p for b in discover.discover(pool, 10, allowed=allowed) for p, *_ in b["photos"]]


def test_quality_rejects_and_picks():
    pool = make_pool()
    pool.quality = np.array([0.5, 0.5, 0.9, 0.05, 0.9, 0.9, 0.9])  # 13 is among the weakest…
    pool.picked = np.zeros(7, bool)
    pool.rejected = np.zeros(7, bool)
    shown = lambda: [p for b in discover.discover(pool, 10) for p, *_ in b["photos"]]  # noqa: E731
    assert 13 not in shown()
    pool.picked[3] = True  # …unless picked: a gem the scores miss
    assert 13 in shown()
    pool.rejected[4] = True  # rejected photos never show
    assert 14 not in shown()


def test_sampling_varies_and_repeats_with_the_seed():
    pool = make_pool()
    # Many candidates for one lens: 40 sheep, alike to the centre by varying amounts.
    n = 40
    extra = discover.Pool(
        ids=np.arange(100, 100 + n), E=np.tile(pool.E[2], (n, 1)), group=-np.arange(100, 100 + n),
        day=[""] * n, taken=np.full(n, np.nan), brightness=np.full(n, 0.5), contrast=np.full(n, 0.2),
        colorfulness=np.full(n, 0.3), hues=np.zeros((n, 24), np.float32), layout=np.zeros((n, discover.GRID ** 2), np.float32),
        things=np.tile([1.0, 0.0], (n, 1)) * np.linspace(1, 0.6, n)[:, None], settings=np.tile([0.0, 1.0], (n, 1)),
        thing_names=pool.thing_names, setting_names=pool.setting_names,
    )
    big = discover.Pool(
        ids=np.concatenate([pool.ids, extra.ids]), E=np.vstack([pool.E, extra.E]), group=np.concatenate([pool.group, extra.group]),
        day=pool.day + extra.day, taken=np.concatenate([pool.taken, extra.taken]),
        brightness=np.concatenate([pool.brightness, extra.brightness]), contrast=np.concatenate([pool.contrast, extra.contrast]),
        colorfulness=np.concatenate([pool.colorfulness, extra.colorfulness]), hues=np.vstack([pool.hues, extra.hues]),
        layout=np.vstack([pool.layout, extra.layout]), things=np.vstack([pool.things, extra.things]),
        settings=np.vstack([pool.settings, extra.settings]), thing_names=pool.thing_names, setting_names=pool.setting_names,
    )
    big.kind = [""] * len(big.ids)
    subject = lambda seed: next(b for b in discover.discover(big, 10, prefs={"subject": 1}, rng=np.random.default_rng(seed)) if b["lens"] == "subject")  # noqa: E731
    runs = {tuple(p for p, *_ in subject(seed)["photos"]) for seed in range(8)}
    assert len(runs) > 1  # walks vary
    assert subject(3) == subject(3)  # the same seed: the same branches (going back looks the same)
    assert len({p for r in runs for p in r}) > discover.PER_LENS  # sampled beyond the plain top 3


def test_traits_and_mirror_on_named_axes():
    pool = make_pool()
    # Two axes: (low, high) = (empty, people) and (daylight, night); the centre is
    # strongly "people" and "night".
    pool.axis_ends = [("empty", "people"), ("daylight", "night")]
    pool.axes = np.array([[2.0, 2.0], [2.0, 2.0], [0.0, 0.0], [1.8, 1.9], [-1.5, 0.0], [-1.8, 1.9], [0.0, -2.0]])
    traits = first(pool, "traits")
    assert traits["photos"][0][0] == 13 and traits["reason"] in ("people · night", "night · people")
    mirror = first(pool, "mirror")
    # Flipped on one of its strong axes, the other kept: people -> empty with the
    # night kept (15), or night -> daylight (16; which axis is drawn at random).
    assert mirror["photos"][0][0] in (15, 16) and "→" in mirror["reason"]
    pool.axes = np.zeros((7, 2))  # nothing distinctive: no traits, no mirror
    lenses = [b["lens"] for b in discover.discover(pool, 10)]
    assert "traits" not in lenses and "mirror" not in lenses


def test_text_axes():
    E = np.array([unit(1, 0), unit(0, 1), unit(1, 1)])
    enc = lambda phrases: np.array([unit(1, 0) if "close" in p else unit(0, 1) for p in phrases])  # noqa: E731
    coords, ends = discover.text_axes(E, enc, axes=[(("close-up", ["close"]), ("wide view", ["wide"]))])
    assert ends == [("wide view", "close-up")] and coords[0, 0] > coords[2, 0] > coords[1, 0]


def test_library_axes_skip_series():
    rng = np.random.default_rng(1)
    # 60 photos: a general factor (axis 0) spread over many days, and a tight series
    # of 20 photos from one day, apart from everything on axis 1.
    E = rng.normal(scale=0.05, size=(60, 8))
    E[:40, 0] += np.linspace(-1, 1, 40)
    E[40:, 0] += rng.uniform(-1, 1, 20)  # the series is spread along the general factor too
    E[40:, 1] += 2.0
    E = E / np.linalg.norm(E, axis=1, keepdims=True)
    day = [f"d{k % 30}" for k in range(40)] + ["series"] * 20
    clusters_ = np.array(list(range(40)) + [40] * 20)  # the series is one cluster: one centre
    P = discover.library_axes(E, clusters_, day, k=3, ends=10)
    assert P is not None and abs(P.mean(axis=0)).max() < 1e-6
    # No axis kept whose ends are the series (it would sort photos by "is it the series").
    for a in range(P.shape[1]):
        top = np.argsort(P[:, a])
        for side in (top[:10], top[-10:]):
            assert sum(day[j] == "series" for j in side) <= 8


def test_at_most_nine_branches_closest_first_out():
    pool = make_pool()
    pool.axis_ends = [("empty", "people"), ("daylight", "night")]
    pool.axes = np.array([[2.0, 2.0], [2.0, 2.0], [0.0, 0.0], [1.8, 1.9], [-1.5, 0.0], [-1.8, 1.9], [0.0, -2.0]])
    pool.pca = pool.axes.copy()
    pool.tags = {0: ["Midsummer"], 2: ["Midsummer"], 5: ["Midsummer"]}
    branches = discover.discover(pool, 10)
    assert len(branches) <= discover.MAX_BRANCHES


def test_prefs_order_lenses_and_add_photos():
    pool = make_pool()
    branches = discover.discover(pool, 10, prefs={"moment": 3})
    assert branches[0]["lens"] == "moment"


def test_tag_lens_uses_the_users_tags():
    pool = make_pool()
    assert "tag" not in [b["lens"] for b in discover.discover(pool, 10)]  # no tags: no branch
    pool.tags = {0: ["Midsummer"], 2: ["Midsummer"], 5: ["Midsummer"]}
    tagged = [p for p, *_ in first(pool, "tag")["photos"]]
    assert tagged and set(tagged) <= {12, 15}


def test_drift():
    pool = make_pool()
    d = discover.step_drift(pool, 0, 2, {})  # to the sheep at night
    assert d["night"] > 0 and d["golden hour"] < 0
    bonus = discover.drift_bonus(pool, 0, d)
    assert bonus[2] > bonus[3]  # further towards night
    assert not discover.drift_bonus(pool, 0, {}).any()


def test_layouts_are_cached_until_the_library_changes(tmp_path):
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    Image.new("L", (40, 30), 0).save(thumbs / "1.jpg")
    im = Image.new("L", (40, 30), 0)
    im.paste(255, (0, 0, 20, 30))  # bright left half
    im.save(thumbs / "2.jpg")
    path = tmp_path / "layout.npz"
    ids = np.array([1, 2])
    L = discover.load_layouts(path, thumbs, ids, (1, 1))
    assert L.shape == (2, discover.GRID * discover.GRID) and L[1, 0] > 0 > L[1, -1]
    (thumbs / "2.jpg").unlink()
    assert np.array_equal(discover.load_layouts(path, thumbs, ids, (1, 1)), L)  # cached
    assert not discover.load_layouts(path, thumbs, ids, (2, 2))[1].any()  # new stamp: recomputed


@pytest.fixture
def client(indexed):
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        yield c


def test_discover_start(client, conn):
    ids = {photo(conn, n)["id"] for n in ("IMG_0001.jpg", "IMG_0002.jpg", "IMG_0002_edit.png", "IMG_0003.png")}
    starts = {client.get("/api/discover/start").json()["id"] for _ in range(20)}
    assert starts <= ids and starts
    one = photo(conn, "IMG_0001.jpg")["id"]
    dated = client.get("/api/discover/start", params={"date_from": "2024-05-01", "date_to": "2024-05-01"}).json()
    assert dated["id"] == one  # within the filters
    assert client.get("/api/discover/start", params={"iso_min": 100000}).status_code == 404


def test_discover_api(client, conn):
    a = photo(conn, "IMG_0001.jpg")["id"]
    res = client.get(f"/api/discover/{a}")
    assert res.status_code == 200
    body = res.json()
    assert body["centre"]["id"] == a
    shown = [p["id"] for b in body["branches"] for p in b["photos"]]
    assert a not in shown and len(shown) == len(set(shown))
    assert all({"lens", "label", "reason", "photos"} <= set(b) for b in body["branches"])
    step = body["branches"][0]["photos"][0]["id"]
    nxt = client.get(f"/api/discover/{step}", params={"trail": str(a), "came_from": a, "prefs": '{"closest": 1}', "seed": 7}).json()
    again = client.get(f"/api/discover/{step}", params={"trail": str(a), "came_from": a, "prefs": '{"closest": 1}', "seed": 7}).json()
    assert nxt["branches"] == again["branches"]  # the same seed: the same walk
    assert a not in [p["id"] for b in nxt["branches"] for p in b["photos"]]
    assert isinstance(nxt["drift"], dict)
    assert client.get(f"/api/discover/{a}", params={"prefs": "nope"}).status_code == 400
    assert client.get("/api/discover/999999").status_code == 404
    # Rejected photos are left out unless asked for.
    others = [photo(conn, n)["id"] for n in ("IMG_0002.jpg", "IMG_0002_edit.png", "IMG_0003.png")]
    client.post("/api/flags", json={"ops": [{"ids": others, "flag": "reject"}]})
    assert not [p for b in client.get(f"/api/discover/{a}").json()["branches"] for p in b["photos"]]
    assert [p for b in client.get(f"/api/discover/{a}", params={"rejects": "true"}).json()["branches"] for p in b["photos"]]
    scoped = client.get(f"/api/discover/{a}", params={"scoped": "true", "iso_min": 100000}).json()
    assert scoped["branches"] == []  # nothing within the filters


def test_place_nearby_and_momentum():
    pool = make_pool()
    # Around a centre in Stockholm: 13 at 50 m (the same place), 14 1 km north, 15 1 km
    # east, 16 2 km north; 12 has no trusted position (history far off: nan).
    lat0, lon0 = 59.33, 18.06
    north_1km, east_1km = 1000 / 111_320, 1000 / (111_320 * np.cos(np.radians(lat0)))
    pool.lat = np.array([lat0, lat0, np.nan, lat0 + 0.00045, lat0 + north_1km, lat0, lat0 + 2 * north_1km])
    pool.lon = np.array([lon0, lon0, np.nan, lon0, lon0, lon0 + east_1km, lon0])
    place = first(pool, "place")
    assert [p for p, *_ in place["photos"]] == [13] and "the same place" in place["reason"]
    nearby = next(b for b in discover.discover(pool, 10, prefs={"nearby": 1}) if b["lens"] == "nearby")
    assert [p for p, *_ in nearby["photos"]][:2] in ([14, 15], [15, 14])  # the nearest first, either way
    assert nearby["reason"].split()[-1] in ("north", "east") and round(nearby["bearing"]) in (0, 90)
    east = next(b for b in discover.discover(pool, 10, prefs={"nearby": 1}, heading=90.0) if b["lens"] == "nearby")
    assert east["photos"][0][0] == 15 and "keeps heading" in east["reason"] and round(east["bearing"]) == 90
    assert discover.compass(225) == "south-west" and discover.distance_text(1500) == "1.5 km"
    pool.lat[0] = np.nan  # the centre without a trusted position: no place branches
    assert not {"place", "nearby"} & {b["lens"] for b in discover.discover(pool, 10)}


def test_uncertain_positions_are_used_but_rank_lower():
    pool = make_pool()
    lat0, lon0 = 59.33, 18.06
    km = 1000 / 111_320
    # 14 and 15 both 2 km north; 14 from camera GPS, 15 an estimate good to 600 m;
    # 16 2 km north but uncertain by 3 km (too vague to compare).
    pool.lat = np.array([lat0, lat0, np.nan, np.nan, lat0 + 2 * km, lat0 + 2 * km, lat0 + 2 * km])
    pool.lon = np.array([lon0, lon0, np.nan, np.nan, lon0, lon0 + 0.0001, lon0])
    pool.uncertainty = np.array([20.0, 20, np.nan, np.nan, 20, 600, 3000])
    nearby = next(b for b in discover.discover(pool, 10, prefs={"nearby": 1}) if b["lens"] == "nearby")
    got = {p: why for p, _, why, _ in nearby["photos"]}
    assert list(got)[:2] == [14, 15]  # the certain one first
    assert got[15].startswith("about ") and not got[14].startswith("about ")
    assert 16 not in got
    # An uncertain position widens "the same place" (15 counts at 300 m when uncertain by 600 m).
    pool.lat[5] = lat0 + 0.3 * km
    place = next(b for b in discover.discover(pool, 10, prefs={"place": 1}) if b["lens"] == "place")
    assert [p for p, *_ in place["photos"]] == [15] and place["reason"].startswith("about the same place")
