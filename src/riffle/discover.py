"""Discover: from one photo to others that are related in one particular way.

Each *lens* scores every photo against the centre and returns a few, with a short
reason. Echoes are alike in one aspect and different otherwise; contrasts share one
aspect and are opposite in another; context lenses use time and place. The aspects:

- what the photo shows and how it looks: its profile over the cluster-name phrases
  (clusters.Namer), z-scored per phrase and kept to the phrases that stand out
  most, split into *things* (subjects) and *settings* (light, style, texture,
  composition);
- colour and light (colors.py): hue histogram, brightness, contrast, colourfulness;
- a layout fingerprint: a 12×12 luminance grid of the thumbnail (``layout_of``);
- the user's own tags (fixed and learned), and time;
- named axes of meaning (TEXT_AXES: close-up ↔ wide view, night ↔ daylight, city ↔
  nature…, a photo's position is its similarity to one end minus the other): shared
  traits match the centre on a few of them, a mirror flips it on one. (The library's
  principal axes were tried first: on the first library they were mostly "which photo
  series is this", named "sheep → water caustics".)

A walk can drift: ``drift`` (phrase weights, from the steps taken so far) gives
candidates that move further the same way a small bonus.

Quality: rejected photos never show; photos in the weakest QUALITY_FLOOR of the
library (Curate's quality: CLIP's good/bad and sharp/blurry, exposure, the taste
model) only when picked; above that, better photos get a small bonus. Each branch
samples its photos from its best candidates, so walks vary (``rng``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .colors import BINS, HUES, WIDTH

GRID = 12  # layout fingerprint: GRID × GRID luminance cells
TOP_PHRASES = 12  # phrases kept per photo in its profile (the ones that stand out most)
PER_LENS = 3  # photos per branch
MAX_BRANCHES = 9  # the graph gets crowded beyond this (and keys 1–9 reach them all)
DRIFT_WEIGHT = 0.15
OTHER_KIND = 0.25  # penalty for a photo ↔ illustration jump in the colour, light, and shape lenses
MIN_COLOUR = 0.04  # colour lenses need this much chroma-weighted hue mass in the centre
QUALITY_FLOOR = 0.3  # the weakest share of the library is left out (unless picked)
QUALITY_WEIGHT = 0.3  # the quality bonus: -0.1 at the floor … +0.15 for the best
SAMPLE_FROM = 4  # a branch samples its photos from this many times as many top candidates
SAMPLE_DECAY = 3.0  # …weighted exp(-rank / SAMPLE_DECAY): the best stay likely
TRAIT_AXES = 2  # shared traits: match on this many of the centre's most distinctive axes
DISTINCT = 1.0  # an axis counts for the centre when it is this many standard deviations out


@dataclass
class Pool:
    """Everything the lenses look at, one row per photo (same order as ``ids``)."""

    ids: np.ndarray  # photo ids
    E: np.ndarray  # CLIP embeddings, normalised
    group: np.ndarray  # stack / duplicate group, else -id: one photo per group in the results
    day: list[str]  # "YYYY-MM-DD" or ""
    taken: np.ndarray  # seconds since the epoch, nan if unknown
    brightness: np.ndarray  # nan if not analysed
    contrast: np.ndarray
    colorfulness: np.ndarray
    hues: np.ndarray  # (n, 24), zeros if not analysed
    layout: np.ndarray | None = None  # (n, GRID*GRID) z-scored, or None
    things: np.ndarray | None = None  # (n, T) sparse profile over thing phrases, rows normalised
    settings: np.ndarray | None = None  # (n, S) the same over setting phrases
    thing_names: list[str] = field(default_factory=list)
    setting_names: list[str] = field(default_factory=list)
    kind: list[str] = field(default_factory=list)  # kind of image per row ("" = photograph or unknown)
    quality: np.ndarray | None = None  # 0..1, a percentile within the library
    picked: np.ndarray | None = None  # bool
    rejected: np.ndarray | None = None  # bool
    axes: np.ndarray | None = None  # (n, axes) positions on TEXT_AXES, in standard deviations
    axis_ends: list[tuple[str, str]] = field(default_factory=list)  # per axis: (low end, high end) phrases
    pca: np.ndarray | None = None  # (n, k) positions on the library's own axes (library_axes), in SDs
    tags: dict[int, list[str]] = field(default_factory=dict)  # row -> the user's tags (fixed, learned), rarest first
    row: dict[int, int] = field(default_factory=dict)

    def __post_init__(self):
        self.row = {int(i): k for k, i in enumerate(self.ids)}


# ---- building blocks -------------------------------------------------------------------


def profile(E: np.ndarray, vecs: np.ndarray, top: int = TOP_PHRASES) -> np.ndarray:
    """Phrase profile: similarity to each phrase z-scored over the library, only each
    photo's ``top`` phrases kept (what stands out for it), rows normalised."""
    raw = E @ vecs.T
    z = (raw - raw.mean(axis=0)) / (raw.std(axis=0) + 1e-6)
    keep = np.argsort(-z, axis=1)[:, :top]
    sparse = np.zeros_like(z)
    np.put_along_axis(sparse, keep, np.clip(np.take_along_axis(z, keep, axis=1), 0, None), axis=1)
    return sparse / (np.linalg.norm(sparse, axis=1, keepdims=True) + 1e-9)


def layout_of(image) -> np.ndarray:
    """The layout fingerprint of a PIL image: GRID × GRID mean luminance, z-scored
    (so exposure doesn't matter, only where light and dark are)."""
    g = np.asarray(image.convert("L").resize((GRID, GRID)), dtype=np.float32).ravel()
    return (g - g.mean()) / (g.std() + 1e-3)


def load_layouts(path: Path, thumbs_dir: Path, ids: np.ndarray, stamp) -> np.ndarray:
    """Layout fingerprints for ``ids``, cached in ``path`` (npz) until ``stamp`` changes;
    computed from the thumbnails (a few seconds per thousand)."""
    from PIL import Image

    stamp = np.array(stamp or (0, 0), dtype=np.int64)
    try:
        with np.load(path) as f:
            if np.array_equal(f["stamp"], stamp) and np.array_equal(f["ids"], ids):
                return f["layout"]
    except (OSError, KeyError, ValueError):
        pass
    out = np.zeros((len(ids), GRID * GRID), np.float32)
    for k, pid in enumerate(ids):
        try:
            with Image.open(thumbs_dir / f"{int(pid)}.jpg") as im:
                out[k] = layout_of(im)
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, ids=ids, layout=out, stamp=stamp)
    tmp.replace(path)
    return out


def _hue_names(hist: np.ndarray, k: int = 2) -> list[str]:
    """The named hues (colors.HUES) that carry most of this histogram's colour."""
    centres = np.arange(BINS) * WIDTH
    out = []
    for name, (deg, _) in HUES.items():
        delta = np.abs(((centres - deg + 180.0) % 360.0) - 180.0)
        out.append((float((hist * np.clip(1 - delta / 30.0, 0, None)).sum()), name))
    out.sort(reverse=True)
    return [n for s, n in out[:k] if s > 0.2 * out[0][0]]


def _intersection(h: np.ndarray, H: np.ndarray) -> np.ndarray:
    """Histogram intersection of one hue histogram with many (each normalised to 1)."""
    a = h / (h.sum() + 1e-9)
    B = H / (H.sum(axis=1, keepdims=True) + 1e-9)
    return np.minimum(a[None, :], B).sum(axis=1)


def _shared(names: list[str], a: np.ndarray, b: np.ndarray, k: int = 2) -> list[str]:
    both = np.minimum(a, b)
    order = np.argsort(-both)[:k]
    return [names[j] for j in order if both[j] > 0]


def _top(names: list[str], a: np.ndarray, k: int = 2) -> list[str]:
    return [names[j] for j in np.argsort(-a)[:k] if a[j] > 0]


def _days_apart(pool: Pool, c: int, j: int) -> float:
    return abs(pool.taken[c] - pool.taken[j]) / 86400 if np.isfinite(pool.taken[c]) and np.isfinite(pool.taken[j]) else np.nan


def _gap(days: float) -> str:
    if not np.isfinite(days):
        return ""
    if days < 1:
        return "the same day"
    if days < 45:
        return f"{round(days)} days apart"
    if days < 700:
        return f"{round(days / 30)} months apart"
    return f"{round(days / 365)} years apart"


# ---- lenses ------------------------------------------------------------------------------
# Each takes (pool, centre row, allowed mask) and returns (scores, minimum, reason(row) -> str);
# rows below the minimum (or not allowed) never show. None: the lens has nothing to offer.

LENS_LABELS = {
    "subject": "Same subject, elsewhere",
    "light": "Same light and mood",
    "colour": "Colour echo",
    "shape": "Shape echo",
    "tag": "Shares a tag",
    "opposite": "Same subject, opposite light",
    "complement": "Complementary colours",
    "moment": "Same day",
    "traits": "Shared traits",
    "mirror": "Mirrored",
    "hidden_traits": "Hidden traits",
    "hidden_mirror": "Mirrored, hidden trait",
    "closest": "Closest",
}


def _other_kind(pool: Pool, c: int) -> np.ndarray:
    """1 where a photo is another kind of image than the centre (photo vs illustration)."""
    if not pool.kind:
        return np.zeros(len(pool.ids))
    return np.array([k != pool.kind[c] for k in pool.kind], dtype=float)


def lens_subject(pool, c, sim, rng=None):
    if pool.things is None:
        return None
    t = pool.things @ pool.things[c]
    s = pool.settings @ pool.settings[c]
    far = np.nan_to_num(np.abs(pool.taken - pool.taken[c]) / 86400 >= 1, nan=1.0)
    score = t - 0.5 * s + 0.1 * far
    return score, 0.45, lambda j: " · ".join(
        _shared(pool.thing_names, pool.things[c], pool.things[j]) or ["the same subject"]
    ) + (f", {_gap(_days_apart(pool, c, j))}" if _gap(_days_apart(pool, c, j)) else "")


def lens_light(pool, c, sim, rng=None):
    if pool.settings is None:
        return None
    s = pool.settings @ pool.settings[c]
    t = pool.things @ pool.things[c]
    tone = np.nan_to_num(np.abs(pool.brightness - pool.brightness[c]) + np.abs(pool.contrast - pool.contrast[c]), nan=0.5)
    score = s - 0.6 * t - 0.8 * tone - OTHER_KIND * _other_kind(pool, c)
    return score, 0.3, lambda j: " · ".join(_shared(pool.setting_names, pool.settings[c], pool.settings[j]) or ["the same light"])


def lens_colour(pool, c, sim, rng=None):
    if pool.hues[c].sum() < MIN_COLOUR:
        return None
    inter = _intersection(pool.hues[c], pool.hues)
    mass = pool.hues.sum(axis=1)
    enough = np.clip(mass / max(pool.hues[c].sum(), 1e-9), 0, 1)  # as colourful, roughly
    tone = np.nan_to_num(np.abs(pool.brightness - pool.brightness[c]), nan=0.3)  # a palette includes its lightness
    score = inter * enough - 0.6 * sim - 0.8 * tone - OTHER_KIND * _other_kind(pool, c)
    return score, 0.05, lambda j: " · ".join(_hue_names(np.minimum(pool.hues[c], pool.hues[j]))) or "the same colours"


def lens_complement(pool, c, sim, rng=None):
    if pool.hues[c].sum() < MIN_COLOUR:
        return None
    opposite = np.roll(pool.hues[c], BINS // 2)
    inter = _intersection(opposite, pool.hues)
    mass = np.clip(pool.hues.sum(axis=1) / max(pool.hues[c].sum(), 1e-9), 0, 1)
    score = inter * mass - 0.3 * sim - OTHER_KIND * _other_kind(pool, c)
    here, there = _hue_names(pool.hues[c], 1), _hue_names(opposite, 1)
    return score, 0.3, lambda j: f"{(here or ['?'])[0]} ↔ {(_hue_names(pool.hues[j], 1) or there or ['?'])[0]}"


def lens_shape(pool, c, sim, rng=None):
    if pool.layout is None or not pool.layout[c].any():
        return None
    corr = pool.layout @ pool.layout[c] / (GRID * GRID)
    score = corr - 0.6 * sim - OTHER_KIND * _other_kind(pool, c)
    return score, 0.25, lambda j: "a similar composition"


def lens_opposite(pool, c, sim, rng=None):
    if pool.things is None or not np.isfinite(pool.brightness[c]):
        return None
    t = pool.things @ pool.things[c]
    db = np.nan_to_num(np.abs(pool.brightness - pool.brightness[c]), nan=0.0)
    dc = np.nan_to_num(np.abs(pool.colorfulness - pool.colorfulness[c]), nan=0.0)
    score = t + 1.5 * np.maximum(db, dc)

    def reason(j):
        subject = " · ".join(_shared(pool.thing_names, pool.things[c], pool.things[j], 1)) or "the same subject"
        if db[j] >= dc[j]:
            return f"{subject}, {'darker' if pool.brightness[j] < pool.brightness[c] else 'brighter'}"
        return f"{subject}, {'muted' if pool.colorfulness[j] < pool.colorfulness[c] else 'more colourful'}"

    return np.where(t > 0.35, score, -np.inf), 0.6, reason


def lens_moment(pool, c, sim, rng=None):
    """The same day, looking different: what else was there. (Places were tried too:
    positions estimated from a location history put everything at home together.)"""
    if not pool.day[c]:
        return None
    same_day = np.array([d == pool.day[c] for d in pool.day])
    score = np.where(same_day, 1.0 - sim, -np.inf)

    def reason(j):
        if not (np.isfinite(pool.taken[j]) and np.isfinite(pool.taken[c])):
            return "the same day"
        minutes = (pool.taken[j] - pool.taken[c]) / 60
        when = "later" if minutes > 0 else "earlier"
        return f"{abs(minutes):.0f} min {when}" if abs(minutes) < 60 else f"{abs(minutes) / 60:.0f} h {when}"

    return score, 0.05, reason


def lens_tag(pool, c, sim, rng=None):
    """Photos sharing the centre's rarest tag of the user's own (fixed or learned; the
    automatic vocabulary tags misfire too often to lead anywhere). Among them the
    alike but not near-copies, so the tag, not the look, is what they share."""
    mine = pool.tags.get(c)
    if not mine:
        return None
    tag = mine[0]
    has = np.array([tag in pool.tags.get(k, ()) for k in range(len(pool.ids))])
    score = np.where(has & (sim < 0.9), sim, -np.inf)
    return score, 0.3, lambda j: tag


def _strong_axes(axes: np.ndarray, c: int, k: int, rng) -> list[int]:
    """k of the centre's distinctive axes (|position| >= DISTINCT), drawn with weight
    |position| (the most distinctive by rank without rng)."""
    coord = np.abs(axes[c])
    cand = np.flatnonzero(coord >= DISTINCT)
    if len(cand) < k:
        return []
    if rng is None:
        return list(cand[np.argsort(-coord[cand])][:k])
    return list(rng.choice(cand, size=k, replace=False, p=coord[cand] / coord[cand].sum()))


def _traits(pool, c, sim, rng, axes, name):
    """Alike on a few of the centre's most distinctive axes, not alike overall."""
    if axes is None:
        return None
    chosen = _strong_axes(axes, c, TRAIT_AXES, rng)
    if not chosen:
        return None
    d = np.sqrt(((axes[:, chosen] - axes[c, chosen]) ** 2).mean(axis=1))
    same_side = np.all(np.sign(axes[:, chosen]) == np.sign(axes[c, chosen]), axis=1)
    score = np.where(same_side, 1.0 - d - 1.2 * sim - OTHER_KIND * _other_kind(pool, c), -np.inf)
    label = name(chosen)
    return score, 0.0, lambda j: label


def _mirror(pool, c, sim, rng, axes, name, crossing=lambda a: False):
    """The centre flipped to the other end of one strong axis, all else kept."""
    if axes is None:
        return None
    chosen = _strong_axes(axes, c, 1, rng)
    if not chosen:
        return None
    a = chosen[0]
    side = np.sign(axes[c, a])
    rest = np.delete(np.arange(axes.shape[1]), a)
    other_side = axes[:, a] * side <= -0.5 * abs(axes[c, a])
    d = np.sqrt(((axes[:, rest] - axes[c, rest]) ** 2).mean(axis=1))
    # Relative to how far photos typically are (independent axes spread wider than
    # overlapping named ones): kept only if closer on the rest than 3/4 of the typical photo.
    closeness = 1.0 - d / max(float(np.median(d)), 0.5)  # (the floor: small pools, many near the centre)
    score = np.where(other_side, closeness - (0 if crossing(a) else OTHER_KIND * _other_kind(pool, c)), -np.inf)
    label = name(a, side)
    return score, 0.25, lambda j: label


def _end(pool, a, side):
    low, high = pool.axis_ends[a] if a < len(pool.axis_ends) else ("", "")
    return (high if side > 0 else low) or f"axis {a + 1}"


def lens_traits(pool, c, sim, rng=None):
    """Shared traits on the named axes: historic and wide, but another place."""
    return _traits(pool, c, sim, rng, pool.axes, lambda chosen: " · ".join(_end(pool, a, pool.axes[c, a]) for a in chosen))


def lens_mirror(pool, c, sim, rng=None):
    """Mirrored on a named axis: the same scene but in nature, colourful instead of muted…
    (Crossing between photos and illustrations is the point only on that axis.)"""
    return _mirror(
        pool, c, sim, rng, pool.axes,
        lambda a, side: f"{_end(pool, a, side)} → {_end(pool, a, -side)}",
        crossing=lambda a: a < len(pool.axis_ends) and "illustration" in pool.axis_ends[a],
    )


def lens_hidden_traits(pool, c, sim, rng=None):
    """Shared traits on the library's own axes (PCA, unnamed)."""
    return _traits(pool, c, sim, rng, pool.pca, lambda chosen: f"{len(chosen)} hidden traits alike")


def lens_hidden_mirror(pool, c, sim, rng=None):
    """Mirrored on one of the library's own axes (PCA, unnamed)."""
    return _mirror(pool, c, sim, rng, pool.pca, lambda a, side: "a hidden trait flipped")


def lens_closest(pool, c, sim, rng=None):
    return sim, 0.0, lambda j: f"similarity {sim[j]:.2f}"


LENSES = {
    "subject": lens_subject,
    "light": lens_light,
    "colour": lens_colour,
    "shape": lens_shape,
    "tag": lens_tag,
    "opposite": lens_opposite,
    "complement": lens_complement,
    "moment": lens_moment,
    "traits": lens_traits,
    "mirror": lens_mirror,
    "hidden_traits": lens_hidden_traits,
    "hidden_mirror": lens_hidden_mirror,
    "closest": lens_closest,
}


def drift_bonus(pool: Pool, c: int, drift: dict[str, float]) -> np.ndarray:
    """How far each photo moves from the centre along the walk's drift (phrase weights)."""
    if not drift or pool.things is None:
        return np.zeros(len(pool.ids))
    names = pool.thing_names + pool.setting_names
    P = np.hstack([pool.things, pool.settings])
    d = np.array([drift.get(n, 0.0) for n in names])
    if not d.any():
        return np.zeros(len(pool.ids))
    d = d / np.linalg.norm(d)
    return (P - P[c]) @ d


def step_drift(pool: Pool, frm: int, to: int, drift: dict[str, float], decay: float = 0.6, keep: int = 10) -> dict[str, float]:
    """The drift after stepping from ``frm`` to ``to``: decayed, plus the move's phrase change."""
    if pool.things is None:
        return drift
    names = pool.thing_names + pool.setting_names
    delta = np.hstack([pool.things[to] - pool.things[frm], pool.settings[to] - pool.settings[frm]])
    merged = {n: decay * w for n, w in drift.items()}
    for j in np.argsort(-np.abs(delta))[:keep]:
        merged[names[j]] = merged.get(names[j], 0.0) + float(delta[j])
    top = sorted(merged.items(), key=lambda kv: -abs(kv[1]))[:keep]
    return {n: round(w, 4) for n, w in top if abs(w) > 1e-3}


def discover(
    pool: Pool,
    centre_id: int,
    trail: list[int] = (),
    allowed: np.ndarray | None = None,
    prefs: dict[str, float] | None = None,
    drift: dict[str, float] | None = None,
    rng: np.random.Generator | None = None,
) -> list[dict]:
    """Branches from the centre: [{lens, label, reason, photos: [(id, score, reason)]}],
    each photo on at most one branch; favoured lenses (``prefs``: lens -> times chosen)
    first and with more photos. With ``rng``, each branch samples from its best
    candidates; without, it takes the best."""
    c = pool.row[centre_id]
    sim = pool.E @ pool.E[c]
    ok = np.ones(len(pool.ids), bool) if allowed is None else allowed.copy()
    for pid in [centre_id, *trail]:
        if pid in pool.row:
            ok &= pool.group != pool.group[pool.row[pid]]
    if pool.rejected is not None:
        ok &= ~pool.rejected
    bonus = DRIFT_WEIGHT * drift_bonus(pool, c, drift or {})
    if pool.quality is not None:
        picked = pool.picked if pool.picked is not None else np.zeros(len(pool.ids), bool)
        ok &= (pool.quality >= QUALITY_FLOOR) | picked
        bonus = bonus + QUALITY_WEIGHT * (pool.quality - 0.5)
    prefs = prefs or {}
    # Favoured lenses first; "closest" (the baseline) last, so it goes first when there are too many.
    order = sorted(LENSES, key=lambda k: (-prefs.get(k, 0), k == "closest"))
    taken: set[int] = set()
    branches = []
    for key in order:
        found = LENSES[key](pool, c, sim, rng)
        if found is None:
            continue
        score, minimum, reason = found
        passed = np.isfinite(score) & ok & (score >= minimum)  # the lens's own floor, before bonuses
        score = np.where(passed, score + (bonus if key != "closest" else 0), -np.inf)
        want = PER_LENS + (1 if prefs.get(key, 0) >= 2 else 0)
        candidates, groups = [], set()
        for j in np.argsort(-score):
            if not np.isfinite(score[j]) or len(candidates) >= (SAMPLE_FROM * want if rng is not None else want):
                break
            if j in taken or pool.group[j] in groups:
                continue
            candidates.append(j)
            groups.add(pool.group[j])
        if rng is not None and len(candidates) > want:
            weights = np.exp(-np.arange(len(candidates)) / SAMPLE_DECAY)
            picks = rng.choice(len(candidates), size=want, replace=False, p=weights / weights.sum())
            candidates = [candidates[k] for k in sorted(picks)]  # still best first
        photos = [(int(pool.ids[j]), float(score[j]), reason(j), float(sim[j])) for j in candidates]
        if photos and len(branches) < MAX_BRANCHES:
            taken.update(pool.row[p] for p, *_ in photos)
            branches.append({"lens": key, "label": LENS_LABELS[key], "reason": photos[0][2], "photos": photos})
    return branches


# Named axes of meaning, each a pair of opposite ends: (name, phrases) at each end. A
# photo's position on an axis is its similarity to one end minus the other.
TEXT_AXES = [
    (("close-up", ["a close-up photo", "a macro photo of a detail"]), ("wide view", ["a wide view", "a panorama of a landscape"])),
    (("night", ["a photo taken at night", "a dark night scene"]), ("daylight", ["a photo in bright daylight", "a sunny day"])),
    (("indoors", ["a photo taken indoors", "inside a room"]), ("outdoors", ["a photo taken outdoors", "outside in the open air"])),
    (("illustration", ["a drawing or illustration", "digital art"]), ("photograph", ["a real photograph", "a camera photo"])),
    (("people", ["a photo of people", "a crowd of people"]), ("empty", ["a photo without any people", "an empty scene"])),
    (("city", ["a city street", "buildings in a city"]), ("nature", ["nature", "a forest or a meadow"])),
    (("calm", ["a calm, minimal photo with empty space", "a simple composition"]), ("busy", ["a busy, cluttered scene", "many things at once"])),
    (("colourful", ["a vivid, colourful photo", "bright saturated colours"]), ("muted", ["a muted, desaturated photo", "a black and white photo"])),
    (("warm light", ["warm golden light", "a sunset"]), ("cool light", ["cool blue light", "an overcast grey day"])),
    (("water", ["water", "the sea or a lake"]), ("dry land", ["dry land", "a road or a field"])),
    (("motion", ["motion and action", "something moving fast"]), ("stillness", ["a still, quiet moment", "a still life"])),
    (("historic", ["a historic old building", "old architecture"]), ("modern", ["modern architecture", "a new building"])),
    (("dramatic", ["a dramatic, moody photo", "dramatic light and shadow"]), ("plain", ["a plain, ordinary snapshot", "flat, even light"])),
    (("sharp", ["a sharp, detailed photo", "crisp details"]), ("dreamy", ["a soft, dreamy photo", "a blurry, hazy image"])),
]


def text_axes(E: np.ndarray, encode_text, axes=TEXT_AXES) -> tuple[np.ndarray, list[tuple[str, str]]]:
    """Coordinates (n, axes) on the named axes, in standard deviations over the
    library, and each axis's (low end, high end) names (low = the second end)."""
    cols, ends = [], []
    for (name_a, phrases_a), (name_b, phrases_b) in axes:
        a = encode_text(phrases_a).mean(axis=0)
        b = encode_text(phrases_b).mean(axis=0)
        v = E @ (a / np.linalg.norm(a)) - E @ (b / np.linalg.norm(b))
        cols.append((v - v.mean()) / (v.std() + 1e-9))
        ends.append((name_b, name_a))
    return np.stack(cols, axis=1), ends


PCA_AXES = 12  # the library's own axes, before dropping series axes
SERIES_DAY_SHARE = 0.8  # an axis whose ends are mostly one shooting day describes a series: dropped


def library_axes(E: np.ndarray, clusters_: np.ndarray, day: list[str], k: int = PCA_AXES, ends: int = 30) -> np.ndarray | None:
    """The library's own axes of variation, as positions (n, axes) in standard deviations.
    PCA runs on one point per group of alike photos (the centres of the fine Similar
    clusters, plus the unclustered photos), so a long series counts once and the axes
    describe how scenes differ, not which series a photo is from; axes with a series at
    either end (its ``ends`` photos mostly from one shooting day) are dropped all the same.
    (First library: the share of one day at the ends fell from 0.67 with every photo
    to 0.53 with cluster centres; random directions: 0.40.)"""
    from collections import Counter

    if len(E) < 3:
        return None
    centres = [E[clusters_ == c].mean(axis=0) for c in sorted(set(clusters_.tolist())) if c >= 0]
    X = np.vstack([np.array(centres).reshape(-1, E.shape[1]), E[clusters_ < 0]])
    X = X / np.linalg.norm(X, axis=1, keepdims=True)
    mu = X.mean(axis=0)
    _, _, Vt = np.linalg.svd(X - mu, full_matrices=False)
    P = (E - mu) @ Vt[: min(k, Vt.shape[0])].T
    keep = []
    n = min(ends, len(E) // 4 or 1)
    for a in range(P.shape[1]):
        order = np.argsort(P[:, a])
        shares = [Counter(day[j] for j in side).most_common(1)[0][1] / n for side in (order[:n], order[-n:])]
        if max(shares) <= SERIES_DAY_SHARE:  # a series at either end makes it a series axis
            keep.append(a)
    if not keep:
        return None
    P = P[:, keep]
    return (P - P.mean(axis=0)) / (P.std(axis=0) + 1e-9)
