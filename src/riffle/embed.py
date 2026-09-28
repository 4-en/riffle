"""OpenCLIP embeddings: one L2-normalised vector per photo, stored as .npy files."""

from __future__ import annotations

import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .config import Config, ModelConfig

log = logging.getLogger(__name__)


def pick_device(pref: str) -> str:
    import torch

    if pref and pref != "auto":
        return pref
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class Clip:
    """Thin wrapper around an OpenCLIP model. ``text_only`` drops the image tower."""

    def __init__(self, mcfg: ModelConfig, text_only: bool = False):
        import open_clip
        import torch

        self.torch = torch
        self.device = pick_device(mcfg.device)
        # "hf-hub:<repo>" models (BioCLIP…) carry their weights: no separate pretrained name.
        model, _, preprocess = open_clip.create_model_and_transforms(
            mcfg.name, pretrained=mcfg.pretrained or None, device=self.device
        )
        model.eval()
        if text_only and hasattr(model, "visual"):
            model.visual = None
            if self.device == "cuda":
                torch.cuda.empty_cache()
        self.model = model
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(mcfg.name)

    def encode_images(self, images) -> np.ndarray:
        torch = self.torch
        batch = torch.stack([self.preprocess(im) for im in images]).to(self.device)
        with torch.no_grad(), torch.autocast(self.device, enabled=self.device == "cuda"):
            feats = self.model.encode_image(batch)
        return _normalise(feats.float().cpu().numpy())

    def encode_text(self, texts: list[str]) -> np.ndarray:
        torch = self.torch
        tokens = self.tokenizer(texts).to(self.device)
        with torch.no_grad(), torch.autocast(self.device, enabled=self.device == "cuda"):
            feats = self.model.encode_text(tokens)
        return _normalise(feats.float().cpu().numpy())


def _normalise(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32)
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.maximum(n, 1e-12)


def embedding_paths(cfg: Config, model_id: str | None = None) -> tuple[Path, Path]:
    model_id = model_id or cfg.model.model_id
    return cfg.embeddings_dir / f"{model_id}.npy", cfg.embeddings_dir / f"{model_id}.ids.npy"


def load_embeddings(cfg: Config, model_id: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    vec_path, ids_path = embedding_paths(cfg, model_id)
    if not vec_path.exists() or not ids_path.exists():
        return np.zeros((0, 0), np.float32), np.zeros((0,), np.int64)
    return np.load(vec_path), np.load(ids_path)


def save_embeddings(cfg: Config, vectors: np.ndarray, ids: np.ndarray, model_id: str | None = None) -> None:
    vec_path, ids_path = embedding_paths(cfg, model_id)
    vec_path.parent.mkdir(parents=True, exist_ok=True)
    for arr, path in ((vectors.astype(np.float32), vec_path), (ids.astype(np.int64), ids_path)):
        tmp = path.with_suffix(".tmp.npy")
        np.save(tmp, arr)
        os.replace(tmp, path)


def embed_photos(conn: sqlite3.Connection, cfg: Config, clip: Clip | None = None, progress=None) -> int:
    """Embed new or changed photos and rewrite the model's embedding files.
    Returns the number of photos embedded."""
    from PIL import Image

    model_id = cfg.model.model_id
    vectors, ids = load_embeddings(cfg, model_id)

    current = {
        r["id"]: r["sha256"]
        for r in conn.execute("SELECT id, sha256 FROM photos WHERE status = 'ok'")
    }
    # Photos in no profile's folders ('hidden') keep their vectors: added back, they need
    # no embedding again. A clean-up (deleted rows), a deleted file or new content drops them.
    kept_rows = {
        r["id"]: r["sha256"]
        for r in conn.execute("SELECT id, sha256 FROM photos WHERE status IN ('ok', 'hidden')")
    }
    done = {
        r["photo_id"]: r["sha256"]
        for r in conn.execute("SELECT photo_id, sha256 FROM embedded WHERE model_id = ?", (model_id,))
    }
    keep = np.array(
        [pid in kept_rows and done.get(int(pid)) == kept_rows[int(pid)] for pid in ids], dtype=bool
    )
    kept_ids = set(int(i) for i in ids[keep])
    todo = [
        pid
        for pid in current
        if pid not in kept_ids and (cfg.previews_dir / f"{pid}.jpg").exists()
    ]

    if not todo and keep.all():
        return 0

    new_vecs: list[np.ndarray] = []
    new_ids: list[int] = []
    if todo:
        clip = clip or Clip(cfg.model)
        bs = cfg.model.batch_size

        def load(pid):
            with Image.open(cfg.previews_dir / f"{pid}.jpg") as im:
                return im.convert("RGB")

        batches = [todo[i : i + bs] for i in range(0, len(todo), bs)]
        bar = progress(total=len(todo), desc="embed") if progress else None
        with ThreadPoolExecutor(max_workers=4) as pool:
            for batch in batches:
                images = list(pool.map(load, batch))
                new_vecs.append(clip.encode_images(images))
                new_ids.extend(batch)
                if bar:
                    bar.update(len(batch))
        if bar:
            bar.close()

    parts = [vectors[keep]] if keep.any() else []
    parts += new_vecs
    all_vecs = np.concatenate(parts) if parts else np.zeros((0, 0), np.float32)
    all_ids = np.concatenate([ids[keep], np.array(new_ids, np.int64)])
    order = np.argsort(all_ids)
    save_embeddings(cfg, all_vecs[order] if len(all_ids) else all_vecs, all_ids[order], model_id)

    conn.execute("DELETE FROM embedded WHERE model_id = ?", (model_id,))
    conn.executemany(
        "INSERT INTO embedded (photo_id, model_id, sha256) VALUES (?, ?, ?)",
        [(int(pid), model_id, kept_rows[int(pid)]) for pid in all_ids],
    )
    conn.commit()
    return len(new_ids)
