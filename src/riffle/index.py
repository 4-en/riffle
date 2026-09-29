"""The `riffle index` and `riffle tag` pipelines."""

from __future__ import annotations

import logging
import time
from typing import Callable

from tqdm import tqdm

from . import db
from .config import Config
from .dupes import compute_phashes, group_duplicates
from .embed import Clip, embed_photos, load_embeddings
from .raws import match_raws
from .scan import scan
from .locate import locate_photos
from .stacks import compute_stacks
from .tags import tag_photos
from .thumbs import build_thumbnails

log = logging.getLogger(__name__)


def progress(iterable=None, **kw):
    return tqdm(iterable, unit="img", dynamic_ncols=True, **kw)


def run_index(cfg: Config, progress=progress, report: Callable[[str], None] = print) -> None:
    """Run every step incrementally. ``progress`` is a tqdm-like factory and
    ``report`` receives one summary line per step."""
    t0 = time.perf_counter()
    conn = db.connect(cfg.db_path)
    try:
        _index_steps(conn, cfg, progress, report)
    finally:
        conn.close()
    report(f"done in {time.perf_counter() - t0:.1f}s")


def _index_steps(conn, cfg: Config, progress, report: Callable[[str], None]) -> None:
    from .profiles import indexed_roots

    r = scan(conn, cfg, progress=progress, roots=indexed_roots(cfg))  # every profile's folders
    report(
        f"scan: {len(r.images)} images ({r.added} new, {r.changed} changed, {r.moved} moved, "
        f"{r.unchanged} unchanged, {r.missing} missing, {r.hidden} in no profile's folders, {r.errors} errors)"
    )
    for folder, n in r.offline:
        report(f"offline: {folder} ({n} photos kept as they were; connect it and index again, or remove it in Settings)")
    if r.metadata_refreshed:
        report(f"metadata: re-read EXIF for {r.metadata_refreshed} photos")

    matched, unmatched = match_raws(conn, cfg, r.raws)
    report(f"raws: {matched} matched, {unmatched} unmatched")

    done, failed = build_thumbnails(conn, cfg, progress=progress)
    report(f"thumbnails: {done} made, {failed} failed")

    # Only load the model when there is something to embed; embed_photos still prunes removed photos.
    clip = None
    if _needs_embedding(conn, cfg):
        report(f"loading model {cfg.model.model_id}")
        clip = Clip(cfg.model)
    embedded = embed_photos(conn, cfg, clip=clip, progress=progress)
    report(f"embeddings: {embedded} computed ({cfg.model.model_id})")

    model_id = cfg.model.model_id
    untagged = conn.execute(
        """SELECT 1 FROM embedded WHERE model_id = ?
           AND NOT EXISTS (SELECT 1 FROM photo_tags WHERE model_id = ?) LIMIT 1""",
        (model_id, model_id),
    ).fetchone()
    if embedded or untagged:
        clip = clip or Clip(cfg.model, text_only=True)
        _tag(conn, cfg, clip, report)

    hashed = compute_phashes(conn, cfg, progress=progress)
    groups = group_duplicates(conn, cfg)
    report(f"duplicates: {hashed} hashed, {groups} groups")

    stacks, stacked = compute_stacks(conn, cfg)
    report(f"stacks: {stacks} stacks covering {stacked} photos")

    located = locate_photos(conn, cfg)
    if located is not None:
        placed = ", ".join(f"{n} {source}" for source, n in sorted(located.items())) or "none"
        report(f"locations: {sum(located.values())} photos placed ({placed})")


def _needs_embedding(conn, cfg: Config) -> bool:
    row = conn.execute(
        """SELECT COUNT(*) FROM photos p
           LEFT JOIN embedded e ON e.photo_id = p.id AND e.model_id = ?
           WHERE p.status = 'ok' AND (e.sha256 IS NULL OR e.sha256 != p.sha256)""",
        (cfg.model.model_id,),
    ).fetchone()
    return row[0] > 0


def _tag(conn, cfg: Config, clip: Clip, report: Callable[[str], None] = print) -> None:
    E, ids = load_embeddings(cfg)
    counts = tag_photos(conn, cfg, E, ids, clip.encode_text)
    summary = ", ".join(f"{n} {family}" for family, n in counts.items())
    report(f"tags: {summary} assignments over {len(ids)} photos")


def run_tag(cfg: Config) -> None:
    t0 = time.perf_counter()
    conn = db.connect(cfg.db_path)
    _tag(conn, cfg, Clip(cfg.model, text_only=True))
    conn.close()
    print(f"done in {time.perf_counter() - t0:.1f}s")


def remove_missing(conn, cfg: Config) -> int:
    """Delete the photos that are in no profile's folders any more ('hidden') or whose
    files are gone ('missing'): their catalogue rows (tags, locations and the rest go
    with them), thumbnails and previews. Their embeddings go at the next index run.
    Returns how many.
    Your flags, tags and captions are kept (they are keyed by content), so a photo
    added again later gets them back."""
    ids = [r[0] for r in conn.execute("SELECT id FROM photos WHERE status IN ('missing', 'hidden')")]
    for pid in ids:
        for d in (cfg.thumbs_dir, cfg.previews_dir):
            (d / f"{pid}.jpg").unlink(missing_ok=True)
    with conn:
        conn.executemany("DELETE FROM embedded WHERE photo_id = ?", [(i,) for i in ids])
        conn.executemany("DELETE FROM photos WHERE id = ?", [(i,) for i in ids])
    return len(ids)
