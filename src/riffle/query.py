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
from pathlib import PurePath, PurePosixPath
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


# ---- file and folder names --------------------------------------------------------
#
# Text search also moves photos whose file name (first) or parent folder (next)
# matches the search to the front, keeping the image ranking within each group.
# Matching is by whole words, so short searches stay precise: "cat" matches
# cat_01.jpg but not catalogue.jpg. Every search word must appear (a plural "s"
# either way is allowed), filler words are ignored, and camera names carry no
# words: IMG, DSC, PXL and frame numbers are skipped, years (1900–2099) are kept.

CAMERA_WORDS = {"img", "dsc", "dscf", "dscn", "dcim", "pxl", "mvimg", "mg", "gopr", "dji", "sam", "vid", "p"}
FILLER_WORDS = {"a", "an", "the", "of", "at", "in", "on", "and", "or", "with", "to", "for", "by", "from"}
_WORD = re.compile(r"[^\W_]+")
_PARTS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+|[^\W\d_a-zA-Z]+")


def _is_year(w: str) -> bool:
    return len(w) == 4 and w.isdigit() and 1900 <= int(w) <= 2099


def name_words(name: str) -> set[str]:
    """Meaningful words of a file or folder name: split at separators, camelCase,
    and letter/digit boundaries; camera prefixes, frame numbers, and letters
    wedged between digits left out."""
    words = set()
    for chunk in _WORD.findall(name):
        for m in _PARTS.finditer(chunk):
            w = m.group().casefold()
            if w in CAMERA_WORDS or (w.isdigit() and not _is_year(w)):
                continue
            # Letters wedged between digits ("4cat7") are part of a code, not a word.
            if not w.isdigit() and m.start() > 0 and m.end() < len(chunk) and chunk[m.start() - 1].isdigit() and chunk[m.end()].isdigit():
                continue
            words.add(w)
    return words


def _query_words(phrase: str) -> set[str]:
    return {w.casefold() for w in _WORD.findall(phrase) if w.casefold() not in FILLER_WORDS}


def _has(words: set[str], w: str) -> bool:
    return w in words or f"{w}s" in words or (w.endswith("s") and w[:-1] in words)


def _matches(phrase_words: set[str], words: set[str]) -> bool:
    return bool(phrase_words) and all(_has(words, w) for w in phrase_words)


def name_matcher(q: str) -> Callable[[str], bool] | None:
    """A test for one name (file stem or folder): true when all words of any
    alternative are in it and no excluded term is. None if nothing to match."""
    positives, negatives = parse(q)
    wanted = [w for w in (_query_words(p) for p in positives) if w]
    unwanted = [w for w in (_query_words(n) for n in negatives) if w]
    if not wanted:
        return None

    def test(name: str) -> bool:
        words = name_words(name)
        return any(_matches(w, words) for w in wanted) and not any(_matches(w, words) for w in unwanted)

    return test


def name_matches(q: str, photos) -> dict[int, str]:
    """{photo id: "file" | "folder"} for photos whose file name or parent folder
    matches ``q``. ``photos``: rows of (id, source, rel_path)."""
    test = name_matcher(q)
    if test is None:
        return {}
    out = {}
    for pid, source, rel_path in photos:
        path = PurePosixPath(rel_path)
        if test(path.stem):
            out[pid] = "file"
        else:
            parent = path.parent.name if str(path.parent) != "." else PurePath(source).name
            if parent and test(parent):
                out[pid] = "folder"
    return out


def text_matches(q: str, captions, tags) -> dict[int, str]:
    """{photo id: "tag" | "caption"} for photos with a fixed tag or a caption that
    matches ``q`` (whole words, like names; a tag match wins). ``captions``: rows of
    (id, text); ``tags``: rows of (id, tag)."""
    test = name_matcher(q)
    if test is None:
        return {}
    out = {}
    for pid, tag in tags:
        if pid not in out and test(tag):
            out[pid] = "tag"
    for pid, text in captions:
        if pid not in out and test(text):
            out[pid] = "caption"
    return out
