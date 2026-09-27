"""Text search queries with alternatives and excluded terms: ``beach | lake -people``,
``street -"parked cars"``.

A term after a minus (at the start of a word) is excluded; everything else is the
search, and ``|`` separates alternatives (each one a phrase of its own). Every
alternative becomes a query vector: the phrase minus half of each excluded term,
which moves photos that show it down the ranking. A photo scores its best match
among the alternatives (see ``score``).

On the first real library the subtraction cleared people from "palace -people" and sailboats from "harbour -boats"; a
stronger subtraction (0.8 and more) drifted to unrelated photos. Filtering by the
similarity to the excluded term instead did not work: CLIP rates "boats" high for
any open water, so it would drop every lake photo from "lake -boats".
"""

from __future__ import annotations

import re
from typing import Callable

import numpy as np

NEGATIVE_WEIGHT = 0.5

# A minus at the start of a word, then a quoted phrase or a single word.
_NEGATIVE = re.compile(r'(?:^|(?<=\s))-(?:"([^"]*)"|(\S+))')


def _phrases(text: str) -> list[str]:
    return [" ".join(part.split()) for part in text.split("|") if part.strip()]


def parse(q: str) -> tuple[list[str], list[str]]:
    """``'beach | lake -dogs -"red cars"'`` -> ``(['beach', 'lake'], ['dogs', 'red cars'])``.
    Hyphenated words (``black-and-white``) and a lone ``-`` stay in the search;
    ``-dogs|cats`` excludes both."""
    negatives = [n for quoted, word in _NEGATIVE.findall(q) for n in _phrases(quoted or word)]
    return _phrases(_NEGATIVE.sub(" ", q)), negatives


def query_vectors(q: str, encode: Callable[[list[str]], np.ndarray]) -> np.ndarray | None:
    """One normalised query vector per alternative (rows), or None if ``q`` has no
    terms at all. With only excluded terms, a single vector points away from them
    (least like them first)."""
    positives, negatives = parse(q)
    if not positives and not negatives:
        return None
    vecs = encode(positives + negatives)
    neg = vecs[len(positives) :].sum(axis=0) if negatives else 0.0
    if positives:
        Q = vecs[: len(positives)] - NEGATIVE_WEIGHT * neg
    else:
        Q = -np.asarray(neg)[None, :]
    return Q / np.maximum(np.linalg.norm(Q, axis=1, keepdims=True), 1e-12)


def score(E: np.ndarray, Q: np.ndarray) -> np.ndarray:
    """Each photo's similarity to its best-matching alternative."""
    return (E @ Q.T).max(axis=1)
