"""Orientation-correct JPEG thumbnails (320 px) and previews (1600 px), named by photo id."""

from __future__ import annotations

import logging
import multiprocessing
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .config import Config
from .images import open_image, resize_long_edge

log = logging.getLogger(__name__)

THUMB_EDGE = 320
PREVIEW_EDGE = 1600


def make_derivatives(src: Path, thumb_path: Path, preview_path: Path) -> tuple[int, int]:
    im = open_image(src, max_edge=PREVIEW_EDGE)
    preview = resize_long_edge(im, PREVIEW_EDGE)
    thumb = resize_long_edge(preview, THUMB_EDGE)
    for img, out, quality in ((preview, preview_path, 88), (thumb, thumb_path, 82)):
        tmp = out.with_suffix(".tmp")
        img.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)
        os.replace(tmp, out)
    return im.size


def _job(args) -> tuple[int, str | None]:
    photo_id, src, thumb, preview = args
    try:
        make_derivatives(Path(src), Path(thumb), Path(preview))
        return photo_id, None
    except Exception as e:  # noqa: BLE001 - recorded on the photo
        return photo_id, f"{type(e).__name__}: {e}"


def pending(conn: sqlite3.Connection, cfg: Config) -> list[tuple[int, str, str, str]]:
    jobs = []
    for r in conn.execute("SELECT id, source, rel_path FROM photos WHERE status = 'ok' ORDER BY id"):
        thumb = cfg.thumbs_dir / f"{r['id']}.jpg"
        preview = cfg.previews_dir / f"{r['id']}.jpg"
        if not (thumb.exists() and preview.exists()):
            jobs.append((r["id"], str(Path(r["source"]) / r["rel_path"]), str(thumb), str(preview)))
    return jobs


def build_thumbnails(conn: sqlite3.Connection, cfg: Config, workers: int | None = None, progress=None) -> tuple[int, int]:
    cfg.thumbs_dir.mkdir(parents=True, exist_ok=True)
    cfg.previews_dir.mkdir(parents=True, exist_ok=True)
    jobs = pending(conn, cfg)
    if not jobs:
        return 0, 0
    workers = workers or min(8, os.cpu_count() or 2)
    done = failed = 0
    ctx = multiprocessing.get_context("spawn")  # fork is unsafe once threads/torch are loaded
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        results = pool.map(_job, jobs, chunksize=1)
        if progress:
            results = progress(results, total=len(jobs), desc="thumbnails")
        for photo_id, error in results:
            if error:
                failed += 1
                log.warning("photo %s: %s", photo_id, error)
                conn.execute(
                    "UPDATE photos SET status = 'error', error = ? WHERE id = ?", (error, photo_id)
                )
            else:
                done += 1
    conn.commit()
    return done, failed
