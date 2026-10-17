"""Tags taught by example photos ("your tags").

From four examples on, a tag is a small classifier on the CLIP embeddings: a logistic
regression of the examples against the photos marked as not belonging and a random
sample of the library (which may hold members, so it counts less). It learns which
direction in the embedding matters (animal ears, not "an anime girl"), where a rule
"close enough to an example" can only draw circles around the examples: tightening
them lost real members, loosening them let in look-alikes, and marking photos as not
belonging carved out their neighbours, members included.

Where the cut lies is the user's choice (a slider, ``cut``), relative to how the
classifier scores its own examples when they are left out of training (5-fold): at
1.0 a photo must score like the typical example (the median), at 0.5 half as well.
No fixed cut suits every tag: on an illustration library, "animal ears" (15 % of the
images) was best at 0.5 (precision 0.70 / recall 0.46; at 1.0: 1.00 / 0.05), one
character (0.25 %) at 1.0 (0.58 / 0.74; at 0.5: 0.02 / 1.00), and neither a corrected
probability ("positive-unlabelled" calibration) nor holding out alike examples together
found both. So the tag dialog shows the photos either side of the cut as it moves.
Strict / Normal / Loose are 1.0 / 0.75 / 0.5.

Measured on an illustration library (11,600 images; an answer key from an independent
tagger, checked by eye), the classifier orders the photos much better than the rule
(average precision, the user's own examples and marks): "animal ears" 0.62 vs 0.46,
one character 0.69 vs 0.27.

With fewer than four examples there is too little to learn from or to check it by, so
the rule stays: a photo belongs when it is close enough to its best-matching example
(a fixed cosine similarity per model, below the stack threshold), and not closer to a
photo marked as not belonging. A negative applies to its whole stack (the server
passes its stack mates). The examples always belong; the negatives never do.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# The rule (fewer than MIN_TO_LEARN examples): below the model's stack threshold, per strictness.
MARGINS = {"strict": 0.05, "normal": 0.08, "loose": 0.12}
NEGATIVE_MARGIN = 0.0  # a photo must be this much closer to an example than to any negative

# The cut: how like its examples a photo must be (1.0: like the typical one), and its presets.
PRESETS = {"strict": 1.0, "normal": 0.75, "loose": 0.5}
CUT_RANGE = (0.2, 1.3)

# The classifier.
MIN_TO_LEARN = 4
BACKGROUND = 2000  # library photos sampled as "probably not"
BACKGROUND_WEIGHT = 0.3  # …counting less than a marked photo (some are members)
REGULARISATION = 1.0  # logistic regression C
FOLDS = 5


def threshold(stack_similarity: float, strictness: str) -> float:
    return stack_similarity - MARGINS.get(strictness, MARGINS["normal"])


def as_cut(level: str | float | None) -> float:
    """A cut from a preset name or a number (clamped to CUT_RANGE)."""
    if isinstance(level, str) or level is None:
        return PRESETS.get(level or "normal", PRESETS["normal"])
    return min(CUT_RANGE[1], max(CUT_RANGE[0], float(level)))


def nearest_preset(cut: float) -> str:
    return min(PRESETS, key=lambda k: abs(PRESETS[k] - cut))


def example_rows(E_row: dict[int, int], example_ids: list[int]) -> list[int]:
    """Rows of the embedding matrix for the examples that are indexed."""
    return [E_row[i] for i in example_ids if i in E_row]


def scores(E: np.ndarray, rows: list[int]) -> np.ndarray:
    """Each photo's similarity to its best-matching example."""
    if not rows or E.size == 0:
        return np.zeros(len(E), dtype=np.float32)
    return (E @ E[rows].T).max(axis=1)


@dataclass
class TagScores:
    """A tag's score for every photo and where the cut lies per strictness."""

    score: np.ndarray  # per row: the classifier's probability, or (the rule) the best example similarity
    typical: float  # the classifier: the median held-out example score; the rule: the stack similarity
    allowed: np.ndarray  # rows that may belong at all (the rule: not closer to a negative)
    rows: list[int]  # the examples
    negative_rows: list[int]
    learned: bool  # a classifier (else the rule)

    def threshold(self, level: str | float | None) -> float:
        """The lowest score that belongs at this cut (or preset). The rule maps the same
        scale onto its similarity margins: 1.0 is Strict (0.05 below the stack
        threshold), 0.75 Normal (0.085), 0.5 Loose (0.12)."""
        f = as_cut(level)
        if self.learned:
            return f * self.typical
        return self.typical - (0.05 + (1.0 - f) * 0.14)

    def members(self, level: str | float | None) -> np.ndarray:
        keep = self.allowed & (self.score >= self.threshold(level))
        if self.negative_rows:
            keep[self.negative_rows] = False
        keep[self.rows] = True
        return keep


def _fit(E: np.ndarray, rows: list[int], negative_rows: list[int], background: np.ndarray):
    from sklearn.linear_model import LogisticRegression

    X = np.concatenate([E[rows], E[negative_rows], E[background]])
    y = np.r_[np.ones(len(rows)), np.zeros(len(negative_rows) + len(background))]
    w = np.r_[np.ones(len(rows) + len(negative_rows)), np.full(len(background), BACKGROUND_WEIGHT)]
    return LogisticRegression(C=REGULARISATION, class_weight="balanced", max_iter=2000).fit(X, y, sample_weight=w)


def tag_scores(E: np.ndarray, rows: list[int], stack_similarity: float, negative_rows: list[int] = ()) -> TagScores:
    """Score every row of ``E`` for a tag with these example rows (and negative rows)."""
    rows = list(dict.fromkeys(rows))
    negative_rows = [r for r in dict.fromkeys(negative_rows) if r not in set(rows)]
    n = len(E)
    if len(rows) < MIN_TO_LEARN or n <= len(rows) + len(negative_rows) + 1:
        s = scores(E, rows)
        allowed = np.ones(n, bool)
        if negative_rows:
            allowed = s > scores(E, negative_rows) + NEGATIVE_MARGIN
        return TagScores(s, stack_similarity, allowed, rows, negative_rows, learned=False)

    # A fixed sample (the same library, the same tag: the same members).
    rng = np.random.default_rng(len(E))
    others = np.setdiff1d(np.arange(n), rows + negative_rows)
    background = np.sort(rng.choice(others, min(BACKGROUND, len(others)), replace=False))
    s = _fit(E, rows, negative_rows, background).predict_proba(E)[:, 1]
    # The examples' scores when each is left out of training: where the cut lies.
    held = []
    order = np.random.default_rng(0).permutation(len(rows))
    for fold in np.array_split(order, min(FOLDS, len(rows))):
        train = [rows[k] for k in order if k not in set(fold)]
        held += list(_fit(E, train, negative_rows, background).predict_proba(E[[rows[k] for k in fold]])[:, 1])
    return TagScores(s, float(np.median(held)), np.ones(n, bool), rows, negative_rows, learned=True)


def members(
    E: np.ndarray, ids: np.ndarray, rows: list[int], stack_similarity: float, level: str | float | None,
    negative_rows: list[int] = (),
) -> tuple[list[int], TagScores | None]:
    """(member photo ids, the tag's scores). Empty without indexed examples."""
    if not rows:
        return [], None
    t = tag_scores(E, rows, stack_similarity, negative_rows)
    return [int(i) for i in ids[t.members(level)]], t
