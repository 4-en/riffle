"""Tags taught by example photos ("your tags").

A photo belongs to a tag when its CLIP embedding is close enough to its best-matching
example: the same rule text tags use for their alternative phrases, so a tag with
varied examples (the same dog on a beach, in snow, indoors) covers each of them,
where an average of the examples would sit between them and match none well. With
one example, the members are that photo's closest matches, like "find similar".

The threshold is a fixed cosine similarity per model, anchored on the stack threshold
(stacks.min_similarity, which is calibrated per model): Normal is 0.08 below it. On
the first real library (ViT-L-14, stacks at 0.92), tags built from three seagull,
fortress, and sheep photos kept to their subject down to about 0.84 and turned into
merely similar scenes (open sky, other waterfront buildings) below 0.80. A threshold
derived from how alike the examples are was worse: near-identical examples made it
far too strict, and varied examples need no lower bar, since each example counts on
its own. The examples themselves always belong.
"""

from __future__ import annotations

import numpy as np

# Below the model's stack threshold, per strictness.
MARGINS = {"strict": 0.05, "normal": 0.08, "loose": 0.12}


def threshold(stack_similarity: float, strictness: str) -> float:
    return stack_similarity - MARGINS.get(strictness, MARGINS["normal"])


def example_rows(E_row: dict[int, int], example_ids: list[int]) -> list[int]:
    """Rows of the embedding matrix for the examples that are indexed."""
    return [E_row[i] for i in example_ids if i in E_row]


def scores(E: np.ndarray, rows: list[int]) -> np.ndarray:
    """Each photo's similarity to its best-matching example."""
    if not rows or E.size == 0:
        return np.zeros(len(E), dtype=np.float32)
    return (E @ E[rows].T).max(axis=1)


def members(E: np.ndarray, ids: np.ndarray, rows: list[int], limit: float) -> tuple[list[int], np.ndarray]:
    """(member photo ids, all scores). Empty without indexed examples."""
    if not rows:
        return [], np.zeros(len(ids), dtype=np.float32)
    s = scores(E, rows)
    keep = s >= limit
    keep[rows] = True  # the examples always belong
    return [int(i) for i in ids[keep]], s
