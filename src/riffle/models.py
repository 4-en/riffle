"""The AI (CLIP) models to choose from in Settings, and fitting the stack similarity to one.

Every model keeps its own embeddings and tags (by ``ModelConfig.model_id``), so
switching back to one used before is instant. General models suit any library; the
specialised ones are better in their field and weaker elsewhere (the everyday tag
vocabulary, styles, the quality hint).

Stacks link consecutive shots at least ``stacks.min_similarity`` alike, and that value
depends on the model. The catalogue has it for the models it was tuned on; for the
others ``calibrate_stack_similarity`` reads it from the library's camera bursts (photos
of one camera at most two seconds apart): on the first library the tuned values sat at
the 17th (ViT-L, 0.92) and 19th (ViT-B, 0.90) percentile of those pairs' similarity.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass

import numpy as np

BURST_SECONDS = 2
BURST_PERCENTILE = 18
MIN_BURST_PAIRS = 50  # fewer: keep the default
DEFAULT_STACK_SIMILARITY = 0.92


@dataclass(frozen=True)
class Model:
    key: str
    label: str
    name: str  # OpenCLIP model name, or "hf-hub:<repo>"
    pretrained: str  # OpenCLIP weights ("" for hf-hub models)
    group: str  # "general" | "nature"
    description: str
    size_gb: float
    speed: str  # indexing speed, relative to the default
    stack_similarity: float | None = None  # tuned value; None: calibrated from the library

    def as_dict(self) -> dict:
        return asdict(self)


CATALOGUE = [
    Model("fast", "Faster", "ViT-B-16", "dfn2b", "general",
          "About four times faster to index, somewhat looser results. The default of the downloadable builds (CPU).",
          0.6, "4× faster", 0.90),
    Model("default", "Default", "ViT-L-14-quickgelu", "dfn2b", "general",
          "Good search, tags and similarity. Fast on a GPU, slow to index on a laptop CPU.",
          1.7, "1×", 0.92),
    Model("best", "Best", "ViT-H-14-quickgelu", "dfn5b", "general",
          "The best general results. About 2.5 times slower than the default and needs about 4 GB of memory.",
          3.9, "2.5× slower"),
    Model("bioclip-2.5", "BioCLIP 2.5", "hf-hub:imageomics/bioclip-2.5-vith14", "", "nature",
          "Plants, animals and fungi: tells species apart and finds them by common or scientific name. "
          "Weaker on everyday scenes, styles and the quality hint.",
          3.9, "2.5× slower"),
    Model("bioclip-2", "BioCLIP 2", "hf-hub:imageomics/bioclip-2", "", "nature",
          "The same focus as BioCLIP 2.5, smaller and faster.",
          1.7, "1×"),
]


def find(name: str, pretrained: str) -> Model | None:
    return next((m for m in CATALOGUE if m.name == name and m.pretrained == (pretrained or "")), None)


def calibrate_stack_similarity(conn: sqlite3.Connection, E: np.ndarray, ids: np.ndarray) -> float | None:
    """The stack similarity for a model, from its embeddings of the library's camera
    bursts; None when the library has too few bursts to tell."""
    from .stacks import exif_seconds

    rows = []
    for pid, camera, taken in conn.execute(
        """SELECT id, camera, taken_at FROM photos WHERE status = 'ok' AND taken_at IS NOT NULL
           AND camera IS NOT NULL AND camera != '' ORDER BY camera, taken_at"""
    ):
        t = exif_seconds(taken)
        if t is not None:
            rows.append((pid, camera, t))
    row = {int(i): k for k, i in enumerate(ids)}
    sims = [
        float(E[row[a[0]]] @ E[row[b[0]]])
        for a, b in zip(rows, rows[1:])
        if a[1] == b[1] and 0 <= b[2] - a[2] <= BURST_SECONDS and a[0] in row and b[0] in row
    ]
    if len(sims) < MIN_BURST_PAIRS:
        return None
    return round(float(np.percentile(sims, BURST_PERCENTILE)), 3)
