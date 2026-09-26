"""Zero-shot tags from vocabulary.yaml, computed from stored embeddings.

Each tag may list alternative phrases; a photo scores a tag by its best-matching phrase.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import yaml

from .config import Config, TagConfig

DEFAULT_TEMPLATES = ["a photo of {}"]


@dataclass
class Label:
    name: str
    phrases: list[str]  # the name itself plus any alternative phrases


@dataclass
class Vocabulary:
    templates: list[str]
    families: dict[str, list[Label]]


def _parse_label(entry) -> Label:
    """A vocabulary entry is either ``name`` or ``{name: [alternative, phrases]}``."""
    if isinstance(entry, dict):
        if len(entry) != 1:
            raise ValueError(f"vocabulary entry must have exactly one name: {entry!r}")
        (name, alts), = entry.items()
        alts = [alts] if isinstance(alts, str) else list(alts or [])
        phrases = [str(name), *(str(a) for a in alts)]
        return Label(str(name), list(dict.fromkeys(phrases)))
    return Label(str(entry), [str(entry)])


def load_vocabulary(path: Path) -> Vocabulary:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    templates = raw.pop("templates", None) or DEFAULT_TEMPLATES
    families = {
        k: [_parse_label(x) for x in v] for k, v in raw.items() if isinstance(v, list) and v
    }
    for family, labels in families.items():
        names = [l.name for l in labels]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate tag names in vocabulary family {family!r}")
    return Vocabulary(templates=templates, families=families)


def phrase_vectors(phrases: list[str], templates: list[str], encode_text: Callable[[list[str]], np.ndarray]) -> np.ndarray:
    """One normalised text vector per phrase, averaged over prompt templates."""
    prompts = [t.format(p) for p in phrases for t in templates]
    enc = encode_text(prompts).reshape(len(phrases), len(templates), -1).mean(axis=1)
    return enc / np.linalg.norm(enc, axis=1, keepdims=True)


def label_similarities(
    E: np.ndarray, labels: list[Label], templates: list[str], encode_text: Callable[[list[str]], np.ndarray]
) -> np.ndarray:
    """(photos, labels) cosine similarity: each label scores its best-matching phrase."""
    phrases = [p for label in labels for p in label.phrases]
    sims = E @ phrase_vectors(phrases, templates, encode_text).T
    out = np.empty((len(E), len(labels)), dtype=np.float32)
    start = 0
    for j, label in enumerate(labels):
        end = start + len(label.phrases)
        out[:, j] = sims[:, start:end].max(axis=1)
        start = end
    return out


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = x - x.max(axis=axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=axis, keepdims=True)


def assign(
    sims: np.ndarray, scale: float, min_prob: float, max_tags: int
) -> list[list[tuple[int, float, float]]]:
    """For each photo, a list of (label index, prob, sim), highest first. The
    softmax runs over labels, so a label's alternative phrases never compete."""
    probs = softmax(sims * scale, axis=1)
    out = []
    for p, s in zip(probs, sims):
        order = np.argsort(-p)[:max_tags]
        out.append([(int(j), float(p[j]), float(s[j])) for j in order if p[j] >= min_prob])
    return out


def tag_photos(
    conn: sqlite3.Connection,
    cfg: Config,
    E: np.ndarray,
    ids: np.ndarray,
    encode_text: Callable[[list[str]], np.ndarray],
    vocab: Vocabulary | None = None,
) -> dict[str, int]:
    """Replace this model's photo_tags rows. Returns tag assignments per family."""
    vocab = vocab or load_vocabulary(cfg.vocabulary_path)
    tcfg: TagConfig = cfg.tags
    model_id = cfg.model.model_id

    conn.execute("DELETE FROM photo_tags WHERE model_id = ?", (model_id,))
    counts: dict[str, int] = {}
    for family, labels in vocab.families.items():
        conn.executemany(
            "INSERT OR IGNORE INTO tags (family, name) VALUES (?, ?)",
            [(family, label.name) for label in labels],
        )
        tag_ids = {
            r["name"]: r["id"]
            for r in conn.execute("SELECT id, name FROM tags WHERE family = ?", (family,))
        }
        counts[family] = 0
        if len(ids) == 0:
            continue
        thresholds = tcfg.families.get(family)
        min_prob = thresholds.min_prob if thresholds else 0.2
        max_tags = thresholds.max_tags if thresholds else 1
        sims = label_similarities(E, labels, vocab.templates, encode_text)
        rows = []
        for pid, picks in zip(ids, assign(sims, tcfg.softmax_scale, min_prob, max_tags)):
            for j, prob, sim in picks:
                rows.append((int(pid), tag_ids[labels[j].name], prob, sim, model_id))
        conn.executemany(
            "INSERT INTO photo_tags (photo_id, tag_id, prob, sim, model_id) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        counts[family] = len(rows)

    # Drop tags no longer in the vocabulary (and their assignments).
    names = [(f, label.name) for f, labels in vocab.families.items() for label in labels]
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _vocab (family TEXT, name TEXT)")
    conn.execute("DELETE FROM _vocab")
    conn.executemany("INSERT INTO _vocab VALUES (?, ?)", names)
    conn.execute(
        "DELETE FROM tags WHERE (family, name) NOT IN (SELECT family, name FROM _vocab)"
    )
    conn.commit()
    return counts
