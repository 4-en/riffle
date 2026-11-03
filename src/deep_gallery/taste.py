"""Personal taste model: which photos this user tends to keep.

Learned from the user's own flags (and exports) on the stored CLIP embeddings,
so it needs no extra labelling and runs in milliseconds.

The unit of learning is a *scene*: a stack of near-identical shots, or a single
photo. A scene with a pick (or an export) is a keeper scene; a scene that was
only rejected is not. Learning per photo would not work: the rejected siblings
of a pick look the same as the pick, so they would teach the model that the
keeper's content is bad (measured on the first real library: AUC 0.74 per photo,
0.82 per scene). Which frame of a keeper scene to keep is left to sharpness and
the suggested keeper; the taste model is weaker at that.

The model is a logistic regression with balanced class weights (L-BFGS). Every
training run is also checked with 5-fold cross-validation over scenes, and the
model is only offered when there is enough data and it clearly helps.

The first training happens when the user asks for it (Settings → Your taste →
Calibrate); after that the server recalibrates it by itself at startup and on a profile
switch when flags changed since, keeping the previous model if the new one does not
pass the check (server.TasteStore). The result is saved next to the embeddings (derived
data) and loaded at startup.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .selections import EXPORTED_EXPR, FLAG_EXPR

MIN_KEEPER_SCENES = 20
MIN_REJECT_SCENES = 50
MIN_AUC = 0.65
C = 1.0  # inverse regularisation strength (best of 0.1..100 on the first real library)


@dataclass
class TasteModel:
    enabled: bool
    reason: str = ""
    keeper_scenes: int = 0
    reject_scenes: int = 0
    auc: float | None = None  # cross-validated, 0.5 = chance
    top20_recall: float | None = None  # share of keeper scenes in the top 20% of scenes
    calibrated_at: float | None = None
    w: np.ndarray | None = field(default=None, repr=False)
    b: float = 0.0

    def score(self, E: np.ndarray) -> np.ndarray:
        """Keep probability (0..1) for rows of normalised embeddings."""
        return 1.0 / (1.0 + np.exp(-(E @ self.w + self.b)))

    def summary(self) -> dict:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "keeper_scenes": self.keeper_scenes,
            "reject_scenes": self.reject_scenes,
            "auc": None if self.auc is None else round(self.auc, 3),
            "top20_recall": None if self.top20_recall is None else round(self.top20_recall, 3),
            "calibrated_at": self.calibrated_at,
        }

    def save(self, path: Path) -> None:
        """Weights (if any) plus the status, written atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.npz")
        meta = json.dumps(self.summary() | {"b": self.b})
        np.savez(tmp, w=self.w if self.w is not None else np.zeros(0), meta=np.array(meta))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "TasteModel | None":
        try:
            with np.load(path) as f:
                meta = json.loads(str(f["meta"]))
                w = f["w"]
        except (OSError, ValueError, KeyError):
            return None
        return cls(
            enabled=meta["enabled"],
            reason=meta.get("reason", ""),
            keeper_scenes=meta.get("keeper_scenes", 0),
            reject_scenes=meta.get("reject_scenes", 0),
            auc=meta.get("auc"),
            top20_recall=meta.get("top20_recall"),
            calibrated_at=meta.get("calibrated_at"),
            w=w if w.size else None,
            b=meta.get("b", 0.0),
        )


def fit_logistic(X: np.ndarray, y: np.ndarray, c: float = C) -> tuple[np.ndarray, float]:
    """L2 logistic regression with balanced class weights."""
    from scipy.optimize import minimize

    sw = np.where(y == 1, 0.5 / y.mean(), 0.5 / (1 - y.mean()))

    def loss(wb):
        w, b = wb[:-1], wb[-1]
        z = X @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        g = sw * (p - y)
        value = np.sum(sw * (np.logaddexp(0, z) - y * z)) + 0.5 / c * (w @ w)
        return value, np.append(X.T @ g + w / c, g.sum())

    r = minimize(loss, np.zeros(X.shape[1] + 1), jac=True, method="L-BFGS-B")
    return r.x[:-1], float(r.x[-1])


def auc(scores: np.ndarray, y: np.ndarray) -> float:
    order = np.argsort(scores)
    ranks = np.empty(len(scores))
    ranks[order] = np.arange(1, len(scores) + 1)
    pos = y == 1
    return float((ranks[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * (~pos).sum()))


def scenes(conn: sqlite3.Connection, E: np.ndarray, ids: np.ndarray):
    """Labelled scenes: (mean embeddings, labels). Needs the selections DB attached."""
    row = {int(i): k for k, i in enumerate(ids)}
    groups: dict[int, list] = {}
    for r in conn.execute(
        f"""SELECT p.id, p.stack_id, {FLAG_EXPR} AS flag, {EXPORTED_EXPR} AS exported
            FROM photos p WHERE p.status = 'ok'"""
    ):
        if r["id"] in row:
            groups.setdefault(r["stack_id"] or -r["id"], []).append(r)
    X, y = [], []
    for members in groups.values():
        keep = any(m["flag"] == "pick" or m["exported"] for m in members)
        rejected = any(m["flag"] == "reject" for m in members)
        if not keep and not rejected:
            continue  # not reviewed yet: no label
        v = np.mean([E[row[m["id"]]] for m in members], axis=0)
        X.append(v / max(np.linalg.norm(v), 1e-12))
        y.append(1.0 if keep else 0.0)
    if not X:
        return np.zeros((0, E.shape[1] if E.ndim == 2 else 0)), np.zeros(0)
    return np.stack(X), np.array(y)


def train(
    conn: sqlite3.Connection,
    E: np.ndarray,
    ids: np.ndarray,
    min_keepers: int = MIN_KEEPER_SCENES,
    min_rejects: int = MIN_REJECT_SCENES,
    min_auc: float = MIN_AUC,
    seed: int = 0,
) -> TasteModel:
    """Train on the user's flags; check it with cross-validation; decide whether to offer it."""
    if E.size == 0:
        return TasteModel(False, "No photos are indexed yet.", calibrated_at=time.time())
    X, y = scenes(conn, E, ids)
    keepers, rejects = int(y.sum()), int(len(y) - y.sum())
    model = TasteModel(False, keeper_scenes=keepers, reject_scenes=rejects, calibrated_at=time.time())
    if keepers < min_keepers or rejects < min_rejects:
        model.reason = (
            f"Needs more flagged photos: {keepers} of {min_keepers} keeper scenes "
            f"and {rejects} of {min_rejects} rejected scenes so far."
        )
        return model

    folds = np.random.default_rng(seed).permutation(len(y)) % 5
    oof = np.zeros(len(y))
    for k in range(5):
        train_idx = folds != k
        if y[train_idx].min() == y[train_idx].max():
            continue
        w, b = fit_logistic(X[train_idx], y[train_idx])
        oof[folds == k] = X[folds == k] @ w + b
    model.auc = auc(oof, y)
    top = np.argsort(-oof)[: max(1, len(y) // 5)]
    model.top20_recall = float(y[top].sum() / keepers)
    if model.auc < min_auc:
        model.reason = (
            f"Your flags do not show a pattern it can learn yet (checked on photos it did not "
            f"learn from: {model.auc:.2f}, where 0.5 is chance and {min_auc:.2f} is needed)."
        )
        return model

    model.w, model.b = fit_logistic(X, y)
    model.enabled = True
    return model
