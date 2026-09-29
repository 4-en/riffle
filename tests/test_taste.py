"""Personal taste model: scenes, training, the quality check, and the API."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from riffle import db, selections, taste
from riffle.embed import save_embeddings
from riffle.server import create_app
from conftest import FakeClip

DIM = 16


def make_library(cfg, n_scenes=120, keeper_share=0.3, signal=True, seed=0):
    """A synthetic catalogue: scenes of 1-3 photos. Keeper scenes lean towards one
    direction in embedding space (when ``signal``); within a keeper stack one frame
    is picked and its near-identical siblings rejected, as in real culling."""
    rng = np.random.default_rng(seed)
    conn = db.connect(cfg.db_path, cfg.selections_path)
    taste_dir = np.zeros(DIM); taste_dir[0] = 1.0
    ids, vecs, picks, rejects, keeper_photos = [], [], [], [], []
    for s in range(n_scenes):
        keeper = rng.random() < keeper_share
        base = rng.normal(size=DIM) + (2.5 * taste_dir if keeper and signal else 0)
        size = int(rng.integers(1, 4))
        members = []
        for k in range(size):
            cur = conn.execute(
                "INSERT INTO photos (rel_path, source, sha256, status) VALUES (?, ?, ?, 'ok')",
                (f"lib{seed}/s{s}_{k}.jpg", str(cfg.sources[0]), f"sha{seed}_{s}_{k}"),  # in the library's folder
            )
            members.append(cur.lastrowid)
            v = base + 0.05 * rng.normal(size=DIM)
            ids.append(cur.lastrowid); vecs.append(v / np.linalg.norm(v))
        if size > 1:
            conn.executemany("UPDATE photos SET stack_id = ? WHERE id = ?", [(min(members), m) for m in members])
        if keeper:
            picks.append(members[0]); rejects += members[1:]; keeper_photos += members
        else:
            rejects += members
    conn.commit()
    selections.set_flags(conn, cfg.selections_path, [(picks, "pick"), (rejects, "reject")])
    save_embeddings(cfg, np.array(vecs, np.float32), np.array(ids))
    make_library.keeper_photos = keeper_photos
    return conn, np.array(vecs, np.float32), np.array(ids), picks


def test_scenes_count_stacks_once_and_skip_unreviewed(cfg):
    conn, E, ids, picks = make_library(cfg, n_scenes=30)
    X, y = taste.scenes(conn, E, ids)
    n_scenes = len({r[0] or -r[1] for r in conn.execute("SELECT stack_id, id FROM photos")})
    assert len(y) == n_scenes and y.sum() == len(picks)  # one label per scene, not per photo
    # Unreviewed scenes have no label; an export alone makes a keeper scene.
    unflag = [int(ids[0])]
    selections.clear_flags(conn, cfg.selections_path, unflag)
    stack = conn.execute("SELECT stack_id FROM photos WHERE id = ?", unflag).fetchone()[0]
    if stack is None:
        assert len(taste.scenes(conn, E, ids)[1]) == n_scenes - 1
    selections.mark_exported(conn, cfg.selections_path, unflag, "/tmp/x")
    X2, y2 = taste.scenes(conn, E, ids)
    assert len(y2) == n_scenes


def test_learns_a_consistent_taste(cfg):
    conn, E, ids, _ = make_library(cfg)
    model = taste.train(conn, E, ids)
    assert model.enabled, model.reason
    assert model.auc > 0.9 and model.top20_recall > 0.5
    scores = model.score(E)
    assert np.all((0 <= scores) & (scores <= 1))


def test_stays_off_without_enough_labels_or_without_a_pattern(cfg, tmp_path):
    conn, E, ids, _ = make_library(cfg, n_scenes=40)
    few = taste.train(conn, E, ids)
    assert not few.enabled and "Needs more flagged photos" in few.reason

    conn2, E2, ids2, _ = make_library(cfg, n_scenes=300, signal=False, seed=3)  # labels unrelated to content
    E_all, ids_all = np.concatenate([E, E2]), np.concatenate([ids, ids2])
    noise = taste.train(conn2, E_all, ids_all)
    assert not noise.enabled and noise.auc is not None and "0.5 is chance" in noise.reason


def test_taste_api_and_sorting(cfg):
    conn, E, ids, picks = make_library(cfg)
    with TestClient(create_app(cfg, text_encoder=FakeClip().encode_text)) as c:
        # Nothing is trained until the user calibrates.
        before = c.get("/api/taste").json()
        assert not before["calibrated"] and not before["enabled"]
        assert c.get("/api/photos", params={"sort": "taste"}).status_code == 409
        status = c.post("/api/taste/calibrate", json={}).json()
        assert status["enabled"] and status["auc"] > 0.9 and status["changed_since"] == 0

        keepers = c.get("/api/photos", params={"sort": "taste", "limit": 1000, "collapse": "none"}).json()["items"]
        scores = [i["taste"] for i in keepers]
        assert scores == sorted(scores, reverse=True) and len(keepers) == len(ids)
        # Scenes are scored as a whole, so a pick's near-identical siblings rank with it:
        # the photos of keeper scenes come first.
        scene_photos = set(make_library.keeper_photos)
        top = {i["id"] for i in keepers[: len(scene_photos)]}
        assert len(top & scene_photos) > 0.85 * len(scene_photos)

        rejects = c.get("/api/photos", params={"sort": "-taste", "limit": 1000, "collapse": "none"}).json()["items"]
        assert [i["id"] for i in rejects] == [i["id"] for i in reversed(keepers)]
        assert c.get(f"/api/photos/{int(ids[0])}").json()["taste"] is not None


def test_calibration_is_saved_and_reports_changes_since(cfg):
    import time

    conn, E, ids, picks = make_library(cfg)
    with TestClient(create_app(cfg, text_encoder=FakeClip().encode_text)) as c:
        c.post("/api/taste/calibrate", json={})
    # A new server (e.g. after a restart) loads the saved model: no retraining.
    with TestClient(create_app(cfg, text_encoder=FakeClip().encode_text)) as c:
        status = c.get("/api/taste").json()
        assert status["enabled"] and status["calibrated"] and status["changed_since"] == 0
        assert c.get("/api/photos", params={"sort": "taste"}).status_code == 200
        time.sleep(0.01)
        c.post("/api/flags", json={"ops": [{"ids": picks[:3], "flag": "reject"}]})
        assert c.get("/api/taste").json()["changed_since"] == 3  # time to recalibrate?
        assert c.post("/api/taste/calibrate", json={}).json()["changed_since"] == 0


def test_taste_sort_is_refused_while_the_model_is_off(cfg):
    make_library(cfg, n_scenes=20)
    with TestClient(create_app(cfg, text_encoder=FakeClip().encode_text)) as c:
        assert not c.post("/api/taste/calibrate", json={}).json()["enabled"]
        res = c.get("/api/photos", params={"sort": "taste"})
        assert res.status_code == 409 and "Needs more flagged photos" in res.json()["detail"]


def test_recalibrated_by_itself_when_flags_changed(cfg):
    """A calibrated model is brought up to date at startup when flags changed since; a
    recalibration that does not pass the check keeps the previous model."""
    import time

    from riffle import selections

    conn, E, ids, picks = make_library(cfg)
    with TestClient(create_app(cfg, text_encoder=FakeClip().encode_text)) as c:
        first = c.post("/api/taste/calibrate", json={}).json()
    time.sleep(0.01)
    selections.set_flags(conn, cfg.selections_path, [(picks[:3], "reject")])  # while the app was not running

    def settle(c):
        deadline = time.time() + 10
        while time.time() < deadline:
            status = c.get("/api/taste").json()
            if status["changed_since"] == 0:
                return status
            time.sleep(0.05)
        return c.get("/api/taste").json()

    with TestClient(create_app(cfg, text_encoder=FakeClip().encode_text)) as c:
        status = settle(c)
        assert status["enabled"] and status["changed_since"] == 0
        assert status["calibrated_at"] > first["calibrated_at"]
    # Now turn nearly every reject into a pick: too few rejects for a model to pass its
    # check, so the old one stays.
    rejected = [r[0] for r in conn.execute("SELECT p.id FROM photos p JOIN sel.flags f ON f.sha256 = p.sha256 WHERE f.flag = 'reject'")]
    time.sleep(0.01)
    selections.set_flags(conn, cfg.selections_path, [(rejected[:-5], "pick")])
    with TestClient(create_app(cfg, text_encoder=FakeClip().encode_text)) as c:
        time.sleep(1.0)
        status = c.get("/api/taste").json()
        assert status["enabled"] and status["changed_since"] > 0  # kept, and still worth a look
