"""Curate: draft a small, high-quality but varied selection (photo book, exhibition).

From the photos within the current filters (picks and unflagged by default, not
rejects), each stack or duplicate group enters once, as its pick or its best
frame. Every candidate gets a quality score ``q`` (rank percentiles within the
candidates, so the scales are comparable): the user's taste model, the CLIP
quality score, exposure, and bonuses for picks and exports; optional style
sliders (CLIP prompt pairs from vocabulary.yaml) add to it. Then a greedy
maximal-marginal-relevance selection trades quality against redundancy with the
photos already chosen: similar content, close in time, close in place (from the
coordinates). Locked photos are kept, removed ones never return (for this draft
only; flags are not touched). Deterministic for the same input.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from .quality import FULL_PENALTY_BLOWN, FULL_PENALTY_CRUSHED
from .selections import EXPORTED_EXPR, FLAG_EXPR
from .stacks import exif_seconds

# Quality weights (with a taste model; without one its weight moves to CLIP quality).
W_TASTE, W_CLIP, W_EXPOSURE = 0.45, 0.25, 0.10
BONUS_PICK, BONUS_EXPORTED = 0.15, 0.05
STYLE_STRENGTH = 0.6  # a style slider at full strength can outweigh the generic quality
QUERY_STRENGTH = 1.0  # a search in Curate: its best matches clearly win, its worst rarely get in
TIME_SCALE = 3 * 3600  # seconds: photos within a few hours count as "close in time"
PLACE_SCALE = 300.0  # metres
CONTENT_FLOOR, CONTENT_DUP = 0.5, 0.95  # CLIP cosine: unrelated below, near-duplicate above


@dataclass
class Params:
    n: int = 12
    variety: float = 0.4  # 0 = best photos, 1 = most varied
    time_spread: float = 0.5
    place_spread: float = 0.5
    styles: dict[str, float] = field(default_factory=dict)  # name -> -1..1
    include_rejects: bool = False
    locked: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)


# Colour and light (colors.py): each choice leans the draft one way, like a style
# slider at full strength. key -> (column, direction, label for the reason text).
LOOKS = {
    "brightness": {1: "bright", -1: "dark"},
    "contrast": {1: "punchy", -1: "soft"},
    "colorfulness": {1: "vivid", -1: "muted"},
}


def look_scores(conn: sqlite3.Connection, look: dict) -> tuple[dict, dict, dict]:
    """Scores, weights and labels for the chosen colour/light preferences, in the
    form of styles: ``({key: {photo id: score}}, {key: weight}, {key: label})``.
    ``look``: ``{"brightness": -1|0|1, "contrast": ..., "colorfulness": ..., "hue": name}``.
    Photos not analysed yet get the median, so they are neither favoured nor dropped."""
    from .colors import HUES, hue_affinity

    wanted = {col: int(look.get(col) or 0) for col in LOOKS if look.get(col) in (1, -1)}
    hue = look.get("hue") if look.get("hue") in HUES else None
    if not wanted and not hue:
        return {}, {}, {}
    rows = conn.execute(
        "SELECT id, brightness, contrast, colorfulness, hues FROM photos WHERE status = 'ok'"
    ).fetchall()
    scores, weights, labels = {}, {}, {}

    def add(key: str, label: str, values: dict[int, float | None]) -> None:
        known = [v for v in values.values() if v is not None]
        fill = float(np.median(known)) if known else 0.0
        scores[key] = {i: (fill if v is None else v) for i, v in values.items()}
        weights[key] = 1.0
        labels[key] = label

    for col, direction in wanted.items():
        add(f"look.{col}", LOOKS[col][direction],
            {r["id"]: None if r[col] is None else direction * r[col] for r in rows})
    if hue:
        degrees = HUES[hue][0]
        add("look.hue", hue, {r["id"]: None if r["hues"] is None else hue_affinity(r["hues"], degrees) for r in rows})
    return scores, weights, labels


@dataclass
class Style:
    name: str
    label: str
    description: str
    towards: list[str]
    away: list[str]


def load_styles(vocabulary_path: Path, default_path: Path) -> list[Style]:
    """Styles from ``styles:`` in vocabulary.yaml, else from the shipped default."""
    for path in (vocabulary_path, default_path):
        try:
            raw = (yaml.safe_load(Path(path).read_text()) or {}).get("styles")
        except OSError:
            continue
        if raw:
            return [
                Style(name, s.get("label", name), s.get("description", ""), list(s["towards"]), list(s["away"]))
                for name, s in raw.items()
                if s.get("towards") and s.get("away")
            ]
    return []


def percentile(values: np.ndarray) -> np.ndarray:
    """Rank percentile in 0..1 (ties share a rank; a single value is 0.5)."""
    n = len(values)
    if n <= 1:
        return np.full(n, 0.5)
    order = values.argsort(kind="stable")
    ranks = np.empty(n)
    ranks[order] = np.arange(n)
    # average the ranks of ties
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    sums = np.bincount(inverse, weights=ranks)
    return (sums / counts)[inverse] / (n - 1)


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6371000 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


@dataclass
class Pool:
    """Candidates (one per stack / duplicate group) with everything the selection needs."""

    ids: np.ndarray  # representative photo ids
    members: list[list[int]]  # all photos of each candidate's group (within the filters)
    E: np.ndarray  # normalised embeddings, row-aligned with ids
    t: np.ndarray  # capture time in seconds, NaN if undated
    lat: np.ndarray
    lon: np.ndarray
    loc_w: np.ndarray  # how much the position can be trusted (0 = no location)
    q: np.ndarray  # quality, including the style term
    picked: np.ndarray
    landscape: np.ndarray
    style_pct: dict[str, np.ndarray]
    info: list[dict]  # per candidate: taken_at, place, day
    query_pct: np.ndarray | None = None  # rank of each candidate for the search (if any)


def build_pool(
    conn: sqlite3.Connection,
    where: str,
    params: list,
    index_row: dict[int, int],
    E_all: np.ndarray,
    p: Params,
    *,
    taste: dict[int, float] | None,
    clip_quality: dict[int, float] | None,
    style_scores: dict[str, dict[int, float]],
    place_labels: dict[str, str],
    query_scores: dict[int, float] | None = None,
) -> Pool | None:
    rows = conn.execute(
        f"""SELECT p.id, p.stack_id, p.dupe_group, p.taken_at, p.width, p.height,
                   p.sharpness, p.clip_highlights, p.clip_shadows,
                   {FLAG_EXPR} AS flag, {EXPORTED_EXPR} AS exported,
                   l.lat, l.lon, l.source AS loc_source, l.accuracy_m,
                   l.country_code, l.region, l.place
            FROM photos p LEFT JOIN photo_locations l ON l.photo_id = p.id
            WHERE {where}""",
        params,
    ).fetchall()
    removed, locked = set(p.removed), set(p.locked)
    groups: dict[int, list] = {}
    for r in rows:
        if r["id"] not in index_row or r["id"] in removed:
            continue
        if r["flag"] == "reject" and not p.include_rejects and r["id"] not in locked:
            continue
        key = r["stack_id"] or r["dupe_group"] or -r["id"]
        groups.setdefault(key, []).append(r)
    if not groups:
        return None

    def frame_score(r) -> float:
        """Which frame represents a group: locked > pick > quality of the frame."""
        exposure = 1 - min(1.0, (r["clip_highlights"] or 0) / FULL_PENALTY_BLOWN + (r["clip_shadows"] or 0) / FULL_PENALTY_CRUSHED)
        base = (clip_quality or {}).get(r["id"], 0.5) + 0.5 * exposure + 0.25 * min(1.0, (r["sharpness"] or 0) / 1000)
        return (10 if r["id"] in locked else 0) + (5 if r["flag"] == "pick" else 0) + base

    reps, members = [], []
    for g in groups.values():
        g.sort(key=lambda r: (-frame_score(r), r["id"]))
        reps.append(g[0])
        members.append([r["id"] for r in g])

    ids = np.array([r["id"] for r in reps], dtype=np.int64)
    E = np.stack([E_all[index_row[int(i)]] for i in ids])
    t = np.array([exif_seconds(r["taken_at"]) if r["taken_at"] else np.nan for r in reps], dtype=float)
    lat = np.array([r["lat"] if r["lat"] is not None else np.nan for r in reps], dtype=float)
    lon = np.array([r["lon"] if r["lon"] is not None else np.nan for r in reps], dtype=float)
    loc_w = np.array([
        0.0 if r["lat"] is None
        else 1.0 if r["loc_source"] in ("exif", "visit")
        else min(1.0, PLACE_SCALE / max(r["accuracy_m"] or PLACE_SCALE, 1.0))
        for r in reps
    ])
    picked = np.array([r["flag"] == "pick" for r in reps])
    exported = np.array([bool(r["exported"]) for r in reps])
    landscape = np.array([(r["width"] or 0) >= (r["height"] or 0) for r in reps])

    # Quality: rank percentiles of the available signals, weighted.
    exposure = np.array([
        1 - min(1.0, (r["clip_highlights"] or 0) / FULL_PENALTY_BLOWN + (r["clip_shadows"] or 0) / FULL_PENALTY_CRUSHED)
        for r in reps
    ])
    q = W_EXPOSURE * percentile(exposure)
    clipq = percentile(np.array([(clip_quality or {}).get(int(i), 0.5) for i in ids]))
    if taste:
        q += W_TASTE * percentile(np.array([taste.get(int(i), 0.0) for i in ids])) + W_CLIP * clipq
    else:
        q += (W_TASTE + W_CLIP) * clipq
    q += BONUS_PICK * picked + BONUS_EXPORTED * exported

    style_pct = {name: percentile(np.array([s.get(int(i), 0.0) for i in ids])) for name, s in style_scores.items()}
    active = {k: w for k, w in p.styles.items() if w and k in style_pct}
    if active:
        term = sum(w * (2 * style_pct[k] - 1) for k, w in active.items()) / len(active)
        q = q + STYLE_STRENGTH * term
    # A search scores (it does not filter): its own term, not averaged with the styles.
    query_pct = percentile(np.array([query_scores.get(int(i), -1.0) for i in ids])) if query_scores else None
    if query_pct is not None:
        q = q + QUERY_STRENGTH * (2 * query_pct - 1)

    info = []
    for r in reps:
        key = f"{r['country_code']}|{r['region']}|{r['place']}" if r["place"] is not None else None
        info.append({
            "taken_at": r["taken_at"],
            "day": r["taken_at"][:10].replace(":", "-") if r["taken_at"] else "",
            "place": place_labels.get(key, "") if key else "",
            "place_key": key or "",
        })
    return Pool(ids, members, E, t, lat, lon, loc_w, q, picked, landscape, style_pct, info, query_pct)


def redundancy_to(pool: Pool, j: int, p: Params) -> np.ndarray:
    """How redundant every candidate would be next to candidate ``j`` (0..1)."""
    content = np.clip((pool.E @ pool.E[j] - CONTENT_FLOOR) / (CONTENT_DUP - CONTENT_FLOOR), 0, 1)
    total, weight = content.copy(), 1.0
    if p.time_spread > 0 and not math.isnan(pool.t[j]):
        close = np.exp(-np.abs(pool.t - pool.t[j]) / TIME_SCALE)
        total += p.time_spread * np.nan_to_num(close, nan=0.0)
        weight += p.time_spread
    if p.place_spread > 0 and pool.loc_w[j] > 0:
        d = haversine(pool.lat, pool.lon, pool.lat[j], pool.lon[j])
        close = np.exp(-np.nan_to_num(d, nan=np.inf) / PLACE_SCALE) * np.minimum(pool.loc_w, pool.loc_w[j])
        total += p.place_spread * close
        weight += p.place_spread
    return total / weight


def select(pool: Pool, p: Params) -> list[int]:
    """Indices into the pool, in selection order (locked first)."""
    n = max(1, min(p.n, len(pool.ids)))
    chosen: list[int] = []
    max_red = np.zeros(len(pool.ids))
    available = np.ones(len(pool.ids), dtype=bool)

    def take(j: int) -> None:
        chosen.append(j)
        available[j] = False
        np.maximum(max_red, redundancy_to(pool, j, p), out=max_red)

    locked = set(p.locked)
    for j, member_ids in enumerate(pool.members):
        if locked & set(member_ids):
            take(j)
    while len(chosen) < n and available.any():
        score = (1 - p.variety) * pool.q - p.variety * max_red
        score[~available] = -np.inf
        take(int(np.argmax(score)))  # argmax takes the first of ties: deterministic
    return chosen


def draft(pool: Pool, p: Params) -> dict:
    """The selection in reading order (chronological), with cover and sections."""
    chosen = select(pool, p)
    order = sorted(chosen, key=lambda j: (math.isnan(pool.t[j]), pool.t[j] if not math.isnan(pool.t[j]) else 0, int(pool.ids[j])))
    cover_pool = [j for j in chosen if pool.landscape[j]] or chosen
    cover = max(cover_pool, key=lambda j: (pool.q[j], -int(pool.ids[j]))) if cover_pool else None
    items, sections, prev = [], [], None
    for j in order:
        head = (pool.info[j]["day"], pool.info[j]["place"])
        if head != prev:
            sections.append({"day": head[0], "place": head[1], "start": len(items)})
            prev = head
        items.append({"index": j, "id": int(pool.ids[j])})
    return {"order": order, "items": items, "cover": int(pool.ids[cover]) if cover is not None else None, "sections": sections}


def reason(pool: Pool, j: int, p: Params, style_labels: dict[str, str]) -> str:
    parts = []
    if pool.picked[j]:
        parts.append("your pick")
    if len(pool.members[j]) > 1:
        parts.append(f"best of {len(pool.members[j])} similar shots")
    if pool.query_pct is not None and pool.query_pct[j] >= 0.9:
        parts.append("a top match for the search")
    strong = [style_labels.get(k, k).lower() for k, w in p.styles.items() if w > 0 and k in pool.style_pct and pool.style_pct[k][j] >= 0.8]
    if strong:
        parts.append("very " + " & ".join(strong))
    return " · ".join(parts)


def alternatives(pool: Pool, slot_id: int, chosen_ids: set[int], p: Params, k: int = 8) -> list[int]:
    """Photos that could take a slot: its other frames first, then unselected
    candidates that are similar to it and good."""
    try:
        j = next(i for i, m in enumerate(pool.members) if slot_id in m)
    except StopIteration:
        return []
    siblings = [m for m in pool.members[j] if m != slot_id]
    near = redundancy_to(pool, j, p)
    score = 0.5 * near + 0.5 * pool.q
    order = np.argsort(-score, kind="stable")
    others = [int(pool.ids[i]) for i in order if int(pool.ids[i]) not in chosen_ids and i != j]
    return (siblings + others)[:k]
