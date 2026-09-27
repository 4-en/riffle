"""Curate: candidates, quality, variety, time/place spread, locks, alternatives, API."""

import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from riffle import curate, db, selections
from riffle.filters import PhotoFilter
from riffle.server import create_app
from conftest import FakeClip

DIM = 16
FLAT = dict(variety=0.0, time_spread=0.0, place_spread=0.0)


class Lib:
    """A synthetic catalogue: rows, embeddings and scores set directly."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = db.connect(cfg.db_path, cfg.selections_path)
        self.vecs: dict[int, np.ndarray] = {}
        self.rng = np.random.default_rng(0)

    def add(self, vec=None, taken="2024:05:01 10:00:00", place=None, stack=None, portrait=False) -> int:
        n = len(self.vecs) + 1
        w, h = (300, 400) if portrait else (400, 300)
        pid = self.conn.execute(
            "INSERT INTO photos (rel_path, source, sha256, taken_at, width, height) VALUES (?, 'src', ?, ?, ?, ?)",
            (f"p{n}.jpg", f"sha{n}", taken, w, h),
        ).lastrowid
        if stack:
            self.conn.execute("UPDATE photos SET stack_id = ? WHERE id IN (?, ?)", (stack, stack, pid))
        if place:
            self.conn.execute(
                "INSERT INTO photo_locations (photo_id, lat, lon, source, place) VALUES (?, ?, ?, 'exif', ?)",
                (pid, place[0], place[1], f"{place[0]:.4f}"),
            )
        v = self.rng.normal(size=DIM) if vec is None else np.asarray(vec, float)
        self.vecs[pid] = v / np.linalg.norm(v)
        self.conn.commit()
        return pid

    def flag(self, ids, value):
        selections.set_flags(self.conn, self.cfg.selections_path, [(list(ids), value)])

    def pool(self, p, quality=None, styles=None, query=None):
        ids = list(self.vecs)
        where, params = PhotoFilter().where("fake__test")
        return curate.build_pool(
            self.conn, where, params, {pid: k for k, pid in enumerate(ids)}, np.stack([self.vecs[i] for i in ids]), p,
            taste=None, clip_quality=quality, style_scores=styles or {}, place_labels={}, query_scores=query,
        )

    def pick(self, quality=None, scores=None, query=None, **kw) -> list[int]:
        p = curate.Params(**kw)
        pool = self.pool(p, quality, scores, query)
        return [int(pool.ids[j]) for j in curate.select(pool, p)]


def near(base, seed, eps=0.02):
    return np.asarray(base, float) + eps * np.random.default_rng(seed).normal(size=DIM)


def axis(k):
    return np.eye(DIM)[k]


def test_rejects_left_out_unless_included(cfg):
    lib = Lib(cfg)
    a, b, c = lib.add(), lib.add(), lib.add()
    lib.flag([b], "reject")
    assert set(lib.pick(n=10)) == {a, c}
    assert set(lib.pick(n=10, include_rejects=True)) == {a, b, c}


def test_a_stack_enters_once_as_its_pick(cfg):
    lib = Lib(cfg)
    s1 = lib.add(near(axis(0), 1))
    s2 = lib.add(near(axis(0), 2), stack=s1)
    s3 = lib.add(near(axis(0), 3), stack=s1)
    other = lib.add(axis(1))
    lib.flag([s2], "pick")
    pool = lib.pool(curate.Params())
    assert len(pool.ids) == 2
    j = list(pool.ids).index(s2)
    assert sorted(pool.members[j]) == [s1, s2, s3]
    assert set(lib.pick(n=5)) == {s2, other}


def test_size_and_best_first(cfg):
    lib = Lib(cfg)
    ids = [lib.add() for _ in range(10)]
    quality = {pid: k / 10 for k, pid in enumerate(ids)}  # later ones are better
    assert lib.pick(quality, n=4, **FLAT) == ids[::-1][:4]
    assert len(lib.pick(quality, n=60)) == 10  # never more than there are


def test_variety_avoids_near_duplicates(cfg):
    lib = Lib(cfg)
    dupes = [lib.add(near(axis(0), s)) for s in range(4)]  # alike, but not stacked
    others = [lib.add(axis(k + 1)) for k in range(4)]
    quality = {**{d: 0.9 for d in dupes}, **{o: 0.5 for o in others}}
    assert set(lib.pick(quality, n=4, **FLAT)) == set(dupes)
    varied = lib.pick(quality, n=4, variety=0.7, time_spread=0, place_spread=0)
    assert len(set(varied) & set(dupes)) == 1


def test_spread_over_time(cfg):
    lib = Lib(cfg)
    base = [lib.add(axis(k), taken=f"2024:05:01 10:{k}0:00") for k in range(6)]  # one hour
    later = [lib.add(axis(6), taken="2024:05:02 12:00:00"), lib.add(axis(7), taken="2024:05:03 12:00:00")]
    quality = {**{d: 0.9 for d in base}, **{d: 0.6 for d in later}}
    assert set(lib.pick(quality, n=3, variety=0.5, time_spread=0, place_spread=0)) <= set(base)
    spread = lib.pick(quality, n=3, variety=0.5, time_spread=1, place_spread=0)
    assert set(spread) & set(later)


def test_spread_over_places(cfg):
    lib = Lib(cfg)
    harbour = [lib.add(axis(k), place=(59.3200 + k * 0.00005, 18.0700)) for k in range(5)]  # one spot
    viewpoint = lib.add(axis(5), place=(59.3400, 18.1000))  # about 2.7 km away
    quality = {**{h: 0.9 for h in harbour}, viewpoint: 0.6}
    assert viewpoint not in lib.pick(quality, n=2, variety=0.7, time_spread=0, place_spread=0)
    assert viewpoint in lib.pick(quality, n=2, variety=0.7, time_spread=0, place_spread=1)


def test_surprise_samples_but_stays_reasonable(cfg):
    lib = Lib(cfg)
    ids = [lib.add() for _ in range(30)]
    quality = {pid: k / 30 for k, pid in enumerate(ids)}  # later ones are better
    best = lib.pick(quality, n=6, **FLAT)
    assert lib.pick(quality, n=6, surprise=0.0, seed=5, **FLAT) == best  # 0: no randomness, whatever the seed
    drafts = [lib.pick(quality, n=6, surprise=1.0, seed=k, **FLAT) for k in range(10)]
    assert len({tuple(sorted(d)) for d in drafts}) > 1  # seeds give different drafts
    assert drafts[3] == lib.pick(quality, n=6, surprise=1.0, seed=3, **FLAT)  # the same seed: the same draft
    worst = set(ids[:10])  # the weakest third never makes it, even at full surprise
    assert not any(worst & set(d) for d in drafts)
    # Removing a photo only changes its slot (the noise is fixed per photo, not per pick).
    removed = drafts[0][0]
    again = lib.pick(quality, n=6, surprise=1.0, seed=0, removed=[removed], **FLAT)
    assert len(set(again) & set(drafts[0])) == 5


def test_locked_and_removed_are_for_the_draft_only(cfg):
    lib = Lib(cfg)
    ids = [lib.add() for _ in range(8)]
    quality = {pid: k / 8 for k, pid in enumerate(ids)}
    kw = dict(n=3, locked=[ids[0]], removed=[ids[-1]], **FLAT)
    got = lib.pick(quality, **kw)
    assert ids[0] in got and ids[-1] not in got and len(got) == 3
    assert got == lib.pick(quality, **kw)  # deterministic
    assert lib.conn.execute("SELECT COUNT(*) FROM sel.flags").fetchone()[0] == 0


def test_locked_photos_stay_outside_the_filters(cfg):
    lib = Lib(cfg)
    a = lib.add(taken="2024:05:01 10:00:00")
    b = lib.add(taken="2024:06:01 10:00:00")
    p = curate.Params(n=2, locked=[b], **FLAT)
    where, params = PhotoFilter(date_to="2024-05-15").where("fake__test")  # b is outside
    ids = list(lib.vecs)
    pool = curate.build_pool(
        lib.conn, where, params, {pid: k for k, pid in enumerate(ids)}, np.stack([lib.vecs[i] for i in ids]), p,
        taste=None, clip_quality=None, style_scores={}, place_labels={},
    )
    assert set(int(pool.ids[j]) for j in curate.select(pool, p)) == {a, b}


def test_locked_reject_stays_even_without_rejects(cfg):
    lib = Lib(cfg)
    a, b = lib.add(), lib.add()
    lib.flag([a], "reject")
    assert a in lib.pick(n=2, locked=[a])


def test_style_weight_moves_the_draft(cfg):
    lib = Lib(cfg)
    ids = [lib.add() for _ in range(10)]
    quality = {pid: 0.5 + k / 100 for k, pid in enumerate(ids)}
    moody = {"moody": {pid: (1.0 if k < 3 else 0.0) - k / 1000 for k, pid in enumerate(ids)}}  # the weakest three
    plain = lib.pick(quality, moody, n=3, **FLAT)
    assert lib.pick(quality, moody, n=3, styles={"moody": 0.0}, **FLAT) == plain
    assert set(lib.pick(quality, moody, n=3, styles={"moody": 1.0}, **FLAT)) == set(ids[:3])
    assert not set(lib.pick(quality, moody, n=3, styles={"moody": -1.0}, **FLAT)) & set(ids[:3])


def test_a_search_scores_but_does_not_filter(cfg):
    lib = Lib(cfg)
    ids = [lib.add() for _ in range(10)]
    quality = {pid: 0.5 + k / 100 for k, pid in enumerate(ids)}  # later ones slightly better
    relevance = {pid: (1.0 if k < 3 else 0.0) - k / 1000 for k, pid in enumerate(ids)}  # the first three match
    assert set(lib.pick(quality, query=relevance, n=3, **FLAT)) == set(ids[:3])
    assert len(lib.pick(quality, query=relevance, n=8, **FLAT)) == 8  # others still fill the draft
    p = curate.Params(n=3, **FLAT)
    pool = lib.pool(p, quality, query=relevance)
    j = list(pool.ids).index(ids[0])
    assert "top match for the search" in curate.reason(pool, j, p, {})
    assert lib.pick(quality, n=3, **FLAT) == ids[::-1][:3]  # without a search: quality


def test_alternatives_start_with_the_stack(cfg):
    lib = Lib(cfg)
    a = lib.add(near(axis(0), 1))
    sib = lib.add(near(axis(0), 2), stack=a)
    rest = [lib.add(axis(k + 1)) for k in range(6)]
    p = curate.Params(n=3)
    pool = lib.pool(p, quality={a: 1.0, sib: 0.9})
    chosen = {int(pool.ids[j]) for j in curate.select(pool, p)}
    assert a in chosen and sib not in chosen
    alts = curate.alternatives(pool, a, chosen, p)
    assert alts[0] == sib
    assert not set(alts) & chosen and set(alts[1:]) <= set(rest)


def test_draft_order_cover_and_sections(cfg):
    lib = Lib(cfg)
    late = lib.add(taken="2024:05:02 09:00:00", place=(59.33, 18.07))
    early = lib.add(taken="2024:05:01 09:00:00", portrait=True)
    mid = lib.add(taken="2024:05:01 18:00:00")
    p = curate.Params(n=3)
    d = curate.draft(lib.pool(p, quality={early: 1.0, mid: 0.5, late: 0.2}), p)
    assert [i["id"] for i in d["items"]] == [early, mid, late]
    assert d["cover"] == mid  # the best landscape photo
    assert [(s["day"], s["start"]) for s in d["sections"]] == [("2024-05-01", 0), ("2024-05-02", 2)]


# ---- API ---------------------------------------------------------------------------


def test_curate_api_remove_alternatives_and_export(indexed, tmp_path):
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        styles = c.get("/api/styles").json()
        assert {"moody", "calm", "scenic"} <= {s["name"] for s in styles}

        d = c.post("/api/curate", json={"n": 2}).json()
        chosen = [i["id"] for i in d["items"]]
        assert len(chosen) == 2 and d["candidates"] >= 3 and d["cover"] in chosen
        assert c.post("/api/curate", json={"n": 2}).json()["items"] == d["items"]  # deterministic

        styled = c.post("/api/curate", json={"n": 2, "styles": {"moody": 1, "nonsense": 1}}).json()
        assert styled["used"]["styles"] == ["moody"]

        alts = c.post("/api/curate/alternatives", json={"n": 2, "id": chosen[0]}).json()["items"]
        assert alts and not {a["id"] for a in alts} & set(chosen)

        after = c.post("/api/curate", json={"n": 2, "removed": [chosen[0]]}).json()
        assert chosen[0] not in {i["id"] for i in after["items"]} and len(after["items"]) == 2
        assert c.get("/api/facets").json()["flag"]["reject"] == 0  # removing is not rejecting

        out = tmp_path / "book"
        res = c.post("/api/export", json={"folder": str(out), "photo_ids": chosen, "location": False})
        assert res.status_code == 200, res.text
        _wait(c)
        copied = sorted(p.name for p in out.rglob("*") if p.is_file() and p.suffix != ".csv")
        names = sorted(Path(i["rel_path"]).name for i in d["items"])
        assert copied == names


def _wait(c):
    deadline = time.time() + 10
    while c.get("/api/export").json()["running"] and time.time() < deadline:
        time.sleep(0.05)


def test_curate_api_with_a_search(indexed):
    from riffle.embed import load_embeddings
    from riffle.query import query_vectors, score

    enc = FakeClip().encode_text
    with TestClient(create_app(indexed, text_encoder=enc)) as c:
        body = {"n": 2, "variety": 0, "time_spread": 0, "place_spread": 0, "query": "red lanterns -blue"}
        d = c.post("/api/curate", params={"dupes": "all"}, json=body).json()
        assert d["used"]["query"] is True
        E, ids = load_embeddings(indexed)
        best = int(ids[np.argmax(score(E, query_vectors(body["query"], enc)))])
        assert best in [i["id"] for i in d["items"]]
        assert "top match for the search" in next(i["reason"] for i in d["items"] if i["id"] == best)
        assert c.post("/api/curate", json={"n": 2}).json()["used"]["query"] is None  # no search given
        assert c.post("/api/curate", json={"n": 2, "query": '-""'}).json()["used"]["query"] is False  # nothing to search for
