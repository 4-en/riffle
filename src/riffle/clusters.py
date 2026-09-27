"""Clusters of similar photos, for the "Similar" grouping.

Average-linkage hierarchical clustering of the CLIP embeddings (cosine distance),
cut at a level: broad, medium, or fine. On the first library (2,132 photos) this
took 0.4 s and gave 23 / 56 / 95 clusters of five or more, covering 98 / 94 / 83 %
of the photos: waterfront, flowers, birds in the sky, sheep, ducks, palace
interiors, illustrations. HDBSCAN left a quarter to a third of the photos
unclustered, so it is not used. Clusters smaller than MIN_SIZE go to "Other".

Clusters are named after the tag nearest their centre (subject and scene tags,
skipping catch-alls), or after the kind of image when most members are not
photographs (illustrations would otherwise be called "a portrait").
"""

from __future__ import annotations

import numpy as np

LEVELS = {"broad": 0.45, "medium": 0.35, "fine": 0.25}  # cosine-distance cuts
MIN_SIZE = 5
MAX_DIRECT = 6000  # above this, cluster a sample (the pairwise distances need memory)
CATCH_ALL = {"other", "ordinary daylight", "a photograph"}
SECOND_NAME_WITHIN = 0.015  # a second tag joins the name when this close to the first


def cluster(E: np.ndarray, level: str = "medium", seed: int = 0) -> np.ndarray:
    """A cluster number per row of ``E`` (normalised embeddings): 0 is the
    largest cluster, 1 the next, …; -1 is "Other". Deterministic."""
    from scipy.cluster.hierarchy import fcluster, linkage

    n = len(E)
    if n < 2:
        return np.full(n, -1)
    cut = LEVELS[level]
    X = np.asarray(E, dtype=np.float64)
    if n <= MAX_DIRECT:
        raw = fcluster(linkage(X, method="average", metric="cosine"), t=cut, criterion="distance")
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
    labels = np.full(n, -1)
    found = [(k, np.flatnonzero(raw == k)) for k in np.unique(raw) if k != 0]
    found = [(k, rows) for k, rows in found if len(rows) >= MIN_SIZE]
    found.sort(key=lambda kr: (-len(kr[1]), kr[1][0]))  # largest first; ties by first row
    for number, (_, rows) in enumerate(found):
        labels[rows] = number
    return labels


def name(
    centroid: np.ndarray,
    tag_names: list[str],
    tag_vecs: np.ndarray | None,
    member_kinds: list[str | None] = (),
    member_tags: list[str] = (),
) -> str:
    """A cluster's name. ``tag_vecs``: subject and scene tag text vectors (rows,
    aligned with ``tag_names``). ``member_kinds``: each member's kind tag, if any.
    ``member_tags``: the members' assigned subject tags (fallback without vectors)."""
    kinds = [k for k in member_kinds if k]
    if kinds:
        top, count = max(((k, kinds.count(k)) for k in set(kinds)), key=lambda kc: (kc[1], kc[0]))
        if top not in CATCH_ALL and count >= len(member_kinds) / 2:
            return top
    if tag_vecs is not None and len(tag_vecs):
        c = centroid / np.linalg.norm(centroid)
        s = tag_vecs @ c
        order = [j for j in np.argsort(-s) if tag_names[j] not in CATCH_ALL]
        if order:
            parts = [tag_names[order[0]]]
            if len(order) > 1 and s[order[1]] >= s[order[0]] - SECOND_NAME_WITHIN:
                parts.append(tag_names[order[1]])
            return " · ".join(parts)
    tags = [t for t in member_tags if t not in CATCH_ALL]
    if tags:
        return max(set(tags), key=lambda t: (tags.count(t), t))
    return "Similar photos"
