"""Clusters of similar photos, for the "Similar" grouping.

Average-linkage hierarchical clustering of the CLIP embeddings (cosine distance),
cut at a level: broad, medium, or fine. On the first library (2,132 photos) this
took 0.4 s and gave 23 / 56 / 95 clusters of five or more, covering 98 / 94 / 83 %
of the photos: waterfront, flowers, birds in the sky, sheep, ducks, palace
interiors, illustrations. HDBSCAN left a quarter to a third of the photos
unclustered, so it is not used. Clusters smaller than MIN_SIZE go to "Other".

Clusters are named from a naming vocabulary (defaults/cluster_names.yaml, ~520
everyday concepts) by the most distinctive phrase: the one the cluster is closest
to compared with the average photo of the library. On the first library, that
named 34 medium clusters "sheep", "ducks", "a palace garden", "a metro station",
"candlelight", "vintage cars"…, where the nearest sidebar tag often gave a generic
or wrong name ("a portrait" for illustrations, "sky and clouds", "grass").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

LEVELS = {"broad": 0.45, "medium": 0.35, "fine": 0.25}  # cosine-distance cuts
MIN_SIZE = 4
MAX_DIRECT = 6000  # above this, cluster a sample (the pairwise distances need memory)
MAX_SHARE = {"broad": 0.20, "medium": 0.10, "fine": 0.05}  # largest share of the view one cluster may hold
MIN_CAP = 10  # ...but never split below this many photos
SPLIT_STEP, MIN_CUT = 0.8, 0.12  # lower the cut by this factor inside a too-large cluster, down to MIN_CUT
ADDED_RAW_WITHIN = 0.02  # ...and an added phrase must be about as similar to the cluster as the name
QUALIFIER_WITHIN = 0.04  # a phrase telling apart same-named clusters must fit about as well
CUSTOM_SHARE = 0.6
# How a kind of image reads as the start of a cluster name.
KIND_LABELS = {"an illustration or drawing": "Illustrations", "a painting": "Paintings", "a document": "Documents"}  # a custom tag names a cluster when this share of its photos has it
CATCH_ALL = {"other", "ordinary daylight", "a photograph"}
# Fixed tags (written on photos, selections.py) name a cluster when one is on this
# share of its photos and this many times as common there as in the clusters it is
# compared with (those of its kind, else the view); at least FIXED_COVERAGE of its
# photos must have fixed tags at all. Characters come first; at most two tags.
FIXED_SHARE, FIXED_LIFT, FIXED_COVERAGE, FIXED_MAX = 0.5, 2.0, 0.5, 2
# Tags about the picture, not what is in it, and booru staples that set nothing apart.
FIXED_SKIP = {
    "artist name", "signature", "watermark", "twitter username", "web address", "artist logo", "dated",
    "patreon username", "commentary request", "highres", "absurdres", "english text",
    "simple background", "white background", "grey background", "gradient background",
    "1girl", "solo", "looking at viewer", "blush", "breasts", "thighs",
}
SECOND_NAME_WITHIN = 0.01  # a second phrase joins the name when this close to the first
SUBJECT_WITHIN = 0.035  # a thing this close behind a setting phrase is named first


def _linkage_groups(X: np.ndarray, rows: np.ndarray, cut: float, cap: float) -> tuple[list[np.ndarray], list[int]]:
    """Clusters (arrays of rows) of ``X[rows]`` at this cut, and the rows left over.
    A cluster above ``cap`` is clustered again with a finer cut; what its parts leave
    over stays together as one more group (it belonged together at the coarser cut)."""
    from scipy.cluster.hierarchy import fcluster, linkage

    labels = fcluster(linkage(X[rows], method="average", metric="cosine"), t=cut, criterion="distance")
    groups, rest = [], []
    for k in np.unique(labels):
        sub = rows[labels == k]
        if len(sub) > cap and cut * SPLIT_STEP >= MIN_CUT:
            parts, leftover = _linkage_groups(X, sub, cut * SPLIT_STEP, cap)
            groups += parts
            if len(leftover) >= MIN_SIZE:
                groups.append(np.array(sorted(leftover)))
            else:
                rest += leftover
        elif len(sub) >= MIN_SIZE:
            groups.append(sub)
        else:
            rest += sub.tolist()
    return groups, rest


def cluster(E: np.ndarray, level: str = "medium", seed: int = 0) -> np.ndarray:
    """A cluster number per row of ``E`` (normalised embeddings): 0 is the
    largest cluster, 1 the next, …; -1 is "Other". Deterministic.

    No cluster may hold more than a share of the photos (MAX_SHARE; the cut is
    lowered inside it until its parts fit). CLIP keeps some kinds of image close
    whatever they show: on the first library all 259 illustrations stayed one
    cluster at the medium cut, even with only illustrations in view; with the cap
    they split by subject (groups of characters, swimwear, armour, the figurine
    photos), 13 groups."""
    from scipy.cluster.hierarchy import fcluster, linkage

    n = len(E)
    if n < 2:
        return np.full(n, -1)
    cut = LEVELS[level]
    cap = max(MIN_CAP, MAX_SHARE[level] * n)
    X = np.asarray(E, dtype=np.float64)
    if n <= MAX_DIRECT:
        groups, _ = _linkage_groups(X, np.arange(n), cut, cap)
    else:
        # Cluster a sample, then give every photo its nearest cluster centre, if
        # it is within the cut; otherwise it is left out ("Other").
        sample = np.sort(np.random.default_rng(seed).choice(n, MAX_DIRECT, replace=False))
        sampled = fcluster(linkage(X[sample], method="average", metric="cosine"), t=cut, criterion="distance")
        keys = np.unique(sampled)
        C = np.stack([X[sample[sampled == k]].mean(axis=0) for k in keys])
        C /= np.linalg.norm(C, axis=1, keepdims=True)
        sims = X @ C.T
        best = sims.argmax(axis=1)
        raw = np.where(1 - sims[np.arange(n), best] <= cut, keys[best], 0)  # 0 is no fcluster label
        groups = []
        for k in np.unique(raw):
            rows = np.flatnonzero(raw == k)
            if k == 0 or len(rows) < MIN_SIZE:
                continue
            if len(rows) > cap and len(rows) <= MAX_DIRECT:
                parts, leftover = _linkage_groups(X, rows, cut * SPLIT_STEP, cap)
                groups += parts + ([np.array(sorted(leftover))] if len(leftover) >= MIN_SIZE else [])
            else:
                groups.append(rows)
    labels = np.full(n, -1)
    groups.sort(key=lambda rows: (-len(rows), int(rows.min())))  # largest first; ties by first row
    for number, rows in enumerate(groups):
        labels[rows] = number
    return labels


@dataclass
class Namer:
    """The naming vocabulary (defaults/cluster_names.yaml) as text vectors, with the
    library's average similarity to each phrase (to find distinctive ones)."""

    phrases: list[str]
    vecs: np.ndarray  # (phrases, dim), normalised
    baseline: np.ndarray  # the view's mean similarity to each phrase (see names)
    setting: np.ndarray  # True for light, style, sky, and texture phrases
    media: np.ndarray | None = None  # True for phrases naming a kind of image (not used within a kind)


def _ranked(namer: Namer, centroid: np.ndarray, skip_media: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Phrases by how distinctive they are for this cluster: closer to its centre
    than the average photo is (on the first library, plain similarity picked
    generic phrases such as "a blue sky" for many clusters)."""
    c = centroid / np.linalg.norm(centroid)
    raw = namer.vecs @ c
    score = raw - namer.baseline
    if skip_media and namer.media is not None:
        score = np.where(namer.media, -np.inf, score)
    return np.argsort(-score, kind="stable"), score, raw


def _fits(raw: np.ndarray, j: int, best: int) -> bool:
    """Whether phrase ``j`` also describes the cluster about as well as the name does.
    Being distinctive is not enough for an added phrase: one that hardly any photo in
    view resembles has a tiny baseline and can tie with real matches ("figurines ·
    peacocks" among illustrations)."""
    return raw[j] >= raw[best] - ADDED_RAW_WITHIN


def _describe(namer: Namer, order: np.ndarray, score: np.ndarray, raw: np.ndarray) -> list[str]:
    best = order[0]
    if namer.setting[best]:
        # A thing close behind a setting names the cluster first ("birds in flight ·
        # a blue sky" rather than just "a blue sky").
        for j in order[1:]:
            if score[j] < score[best] - SUBJECT_WITHIN:
                break
            if not namer.setting[j]:
                return [namer.phrases[j], namer.phrases[best]]
    parts = [namer.phrases[best]]
    second = order[1] if len(order) > 1 else None
    if second is not None and score[second] >= score[best] - SECOND_NAME_WITHIN and _fits(raw, second, best):
        parts.append(namer.phrases[second])
    return parts


def _tag_key(tag: str) -> str:
    return " ".join(tag.replace("_", " ").casefold().split())


def _fixed_name(cluster: list[list[str]], compare: list[list[str]], characters: frozenset[str]) -> list[str] | None:
    """The fixed tags that name a cluster (``cluster`` / ``compare``: tags per photo), or None."""
    if not cluster or sum(1 for tags in cluster if tags) < FIXED_COVERAGE * len(cluster):
        return None
    shown: dict[str, str] = {}
    inside: Counter = Counter()
    for tags in cluster:
        keys = {_tag_key(t) for t in tags}
        inside.update(keys)
        for t in tags:
            shown.setdefault(_tag_key(t), t)
    outside = Counter(k for tags in compare for k in {_tag_key(t) for t in tags})
    picks = []
    for key, n in inside.items():
        share, base = n / len(cluster), outside[key] / max(len(compare), 1)
        if key in FIXED_SKIP or share < FIXED_SHARE or base <= 0 or share / base < FIXED_LIFT:
            continue
        picks.append((key in characters, round(share - base, 6), len(key.split()), key))
    picks.sort(reverse=True)  # characters first, then how much more common it is here, then the more specific
    chosen: list[str] = []
    for *_, key in picks:
        words = set(key.split())
        # "black choker" says what "choker" would add: skip tags within a chosen one (or the reverse).
        if any(words <= set(c.split()) or set(c.split()) <= words for c in chosen):
            continue
        chosen.append(key)
        if len(chosen) == FIXED_MAX:
            break
    return [shown[k] for k in chosen] or None


def names(
    centroids: list[np.ndarray],
    namer: Namer | None,
    member_kinds: list[list[str | None]],
    member_tags: list[list[str]] | None = None,
    member_custom: list[list[list[str]]] | None = None,
    member_fixed: list[list[list[str]]] | None = None,
    characters: frozenset[str] = frozenset(),
) -> list[str]:
    """A name per cluster (clusters in the same order as ``centroids``). Names say
    what sets a cluster apart from the rest of the view: ``namer.baseline`` is the
    view's mean similarity to each phrase, so among illustrations only, "an anime
    illustration" names none of them and their subjects do.

    - When most members (CUSTOM_SHARE) have the same custom tag, the tag names it:
      it is the user's own, most specific word for them.
    - Else fixed tags (``member_fixed``: per cluster, per photo) that are common in
      the cluster and rare among the clusters it is compared with (see FIXED_*), a
      character first (``characters``: tags known to be characters, e.g. from the
      WD tagger's list); after the kind prefix when there is one.
    - When most members are not photographs (the kind tag), and that kind does not
      also fill most of the view, the kind names it ("Illustrations"; plain
      similarity called those "a portrait"). Several clusters of one kind add what
      sets each apart from the others of it ("Illustrations: swimmers").
    - Otherwise the most distinctive phrase of the naming vocabulary; a thing close
      behind a light/style/sky phrase comes first.
    - Without the model: the members' most common subject tag.
    Clusters that would share a name get their next distinctive phrase added."""
    all_kinds = [k for ks in (member_kinds or []) for k in ks]
    view_kind = max(set(all_kinds), key=all_kinds.count) if all_kinds else None
    if view_kind is not None and all_kinds.count(view_kind) < len(all_kinds) / 2:
        view_kind = None  # no kind dominates the view
    sizes = [len(member_kinds[i]) if member_kinds else 1 for i in range(len(centroids))]

    # First, what names each cluster: a custom tag, its kind of image, or its content.
    source: list[tuple[str, str | None]] = []
    for i in range(len(centroids)):
        custom = [t for tags in (member_custom[i] if member_custom else []) for t in tags]
        mine = max(set(custom), key=lambda t: (custom.count(t), t)) if custom else None
        kinds = [k for k in member_kinds[i] if k] if member_kinds else []
        top = max(set(kinds), key=lambda k: (kinds.count(k), k)) if kinds else None
        if mine and custom.count(mine) >= CUSTOM_SHARE * max(sizes[i], 1):
            source.append(("custom", mine))
        elif top and top not in CATCH_ALL and top != view_kind and kinds.count(top) >= sizes[i] / 2:
            source.append(("kind", top))
        else:
            source.append(("content", None))

    # Several clusters of one kind (e.g. illustrations among photos): each is named by
    # what sets it apart from the others of that kind, after the kind ("Illustrations:
    # swimmers"); against the whole view, the kind itself would be the name of all.
    kind_baseline = {}
    if namer is not None:
        for kind in {k for what, k in source if what == "kind"}:
            members = [i for i, (what, k) in enumerate(source) if what == "kind" and k == kind]
            if len(members) > 1:
                mean = sum(sizes[i] * np.asarray(centroids[i]) for i in members) / sum(sizes[i] for i in members)
                kind_baseline[kind] = mean @ namer.vecs.T

    # Fixed tags, compared with the other clusters of the same kind (else the view),
    # so "1girl" does not name every illustration cluster among photos.
    kind_groups: dict[str, list[int]] = {}
    for i, (what, k) in enumerate(source):
        if what == "kind":
            kind_groups.setdefault(k, []).append(i)
    fixed_names: list[list[str] | None] = [None] * len(centroids)
    if member_fixed:
        everyone = [tags for per_photo in member_fixed for tags in per_photo]
        for i, (what, value) in enumerate(source):
            if what == "custom":
                continue
            group = kind_groups.get(value, []) if what == "kind" and len(kind_groups.get(value, [])) > 1 else None
            compare = [tags for j in group for tags in member_fixed[j]] if group else everyone
            fixed_names[i] = _fixed_name(member_fixed[i], compare, characters)

    # Each name as (prefix, phrases): the phrases are what the duplicate check compares.
    out: list[tuple[str | None, list[str]]] = []
    rankings = []
    for i, centroid in enumerate(centroids):
        what, value = source[i]
        ranking = None
        if namer is not None:
            if value in kind_baseline:
                local = Namer(namer.phrases, namer.vecs, kind_baseline[value], namer.setting, namer.media)
                ranking = _ranked(local, centroid, skip_media=True)  # "Illustrations: an anime illustration" says nothing
            else:
                ranking = _ranked(namer, centroid)
        rankings.append(ranking)
        if what == "custom":
            out.append((None, [value]))
        elif fixed_names[i]:
            out.append((KIND_LABELS.get(value, value) if what == "kind" else None, fixed_names[i]))
        elif what == "kind":
            label = KIND_LABELS.get(value, value)
            out.append((label, _describe(namer, *ranking)) if value in kind_baseline else (None, [label]))
        elif ranking is not None:
            out.append((None, _describe(namer, *ranking)))
        else:
            tags = [t for t in (member_tags[i] if member_tags else []) if t not in CATCH_ALL]
            out.append((None, [max(set(tags), key=lambda t: (tags.count(t), t))] if tags else ["Similar photos"]))

    # Tell apart clusters that got the same name: add their next phrase that no other
    # name uses, if it fits nearly as well; otherwise a number ("swimmers 2"). Any
    # unused phrase would do it, but far down the ranking it misleads ("figurines ·
    # peacocks" on the first library).
    display = lambda prefix, parts: (f"{prefix}: " if prefix else "") + " · ".join(parts)  # noqa: E731
    used = {p for _, parts in out for p in parts}
    count: dict[str, int] = {}
    numbered: dict[str, int] = {}  # the last number given per name (numbers run 2, 3, …)
    result = []
    for i, (prefix, parts) in enumerate(out):
        key = display(prefix, parts)
        count[key] = count.get(key, 0) + 1
        if count[key] > 1:
            extra = None
            if rankings[i] is not None:
                order, score, raw = rankings[i]
                top = score[order[0]]
                extra = next(
                    (namer.phrases[j] for j in order
                     if namer.phrases[j] not in used and np.isfinite(score[j])
                     and score[j] >= top - QUALIFIER_WITHIN and _fits(raw, j, order[0])),
                    None,
                )
            if extra:
                used.add(extra)
                result.append(display(prefix, [*parts, extra]))
            else:
                numbered[key] = numbered.get(key, 1) + 1
                result.append(f"{key} {numbered[key]}")
        else:
            result.append(key)
    return result


# ---- 2D map ------------------------------------------------------------------------
#
# The Similar map places every photo so that alike photos sit close together: t-SNE
# on the embeddings (cosine), 1.8 s for the first library's 2,132 photos. It is laid
# out once for the whole library (cached per embedding file), so photos keep their
# places when the filters change. UMAP was not used: it needs numba (heavy, slow to
# start, awkward in the standalone builds).


LAYOUT_DIRECT = 15000  # above this, t-SNE runs on a sample and the rest is placed by it
PLACE_NEIGHBOURS = 5  # …near the sampled photos most like each


def layout(E: np.ndarray, seed: int = 0) -> np.ndarray:
    """2D positions (rows, scaled into 0..1) for rows of normalised embeddings.

    t-SNE on every photo up to LAYOUT_DIRECT; above that on a random sample of that
    size, and each other photo is placed among the sampled photos most like it (their
    positions weighted by similarity, sharply, so it lands in its neighbourhood rather
    than between two), with a small offset so identical ones do not pile up. At 81,000
    photos: 158 s with every photo, 26 s with a sample (the same groups, as coherent)."""
    n = len(E)
    if n == 0:
        return np.zeros((0, 2))
    if n < 4:  # t-SNE needs a few points; a small line is fine for a handful
        return np.column_stack([np.linspace(0.2, 0.8, n) if n > 1 else [0.5], np.full(n, 0.5)])
    from sklearn.manifold import TSNE

    X = np.asarray(E, dtype=np.float32)
    rng = np.random.default_rng(seed)
    sample = np.sort(rng.choice(n, LAYOUT_DIRECT, replace=False)) if n > LAYOUT_DIRECT else np.arange(n)
    m = len(sample)
    perplexity = min(30.0, (m - 1) / 3)
    Ys = TSNE(n_components=2, metric="cosine", perplexity=perplexity, init="pca", random_state=seed).fit_transform(X[sample])
    Y = np.zeros((n, 2), dtype=np.float64)
    Y[sample] = Ys
    if m < n:
        rest = np.setdiff1d(np.arange(n), sample)
        S = X[sample]
        # A typical spacing between sampled neighbours, for the offset.
        spread = float(np.median(np.ptp(Ys, axis=0))) / np.sqrt(m)
        for start in range(0, len(rest), 4096):
            rows = rest[start : start + 4096]
            sims = X[rows] @ S.T
            near = np.argpartition(-sims, PLACE_NEIGHBOURS, axis=1)[:, :PLACE_NEIGHBOURS]
            s = np.take_along_axis(sims, near, axis=1)
            w = np.exp((s - s.max(axis=1, keepdims=True)) * 50.0)  # the nearest dominates
            w /= w.sum(axis=1, keepdims=True)
            Y[rows] = (w[:, :, None] * Ys[near]).sum(axis=1) + rng.normal(scale=0.5 * spread, size=(len(rows), 2))
    lo, hi = Y.min(axis=0), Y.max(axis=0)
    span = np.where(hi - lo > 0, hi - lo, 1.0).max()  # one scale for both axes keeps the shape
    return (Y - lo) / span
