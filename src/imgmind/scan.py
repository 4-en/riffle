"""Walk sources, record files, read EXIF, and apply the photo identity rules.

Originals are only ever opened for reading.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .config import Config
from .images import open_image  # noqa: F401  (registers optional HEIC support)

log = logging.getLogger(__name__)

# EXIF tag ids
ORIENTATION = 0x0112
MAKE, MODEL, DATETIME = 0x010F, 0x0110, 0x0132
EXIF_IFD, GPS_IFD = 0x8769, 0x8825
DATETIME_ORIGINAL, OFFSET_TIME_ORIGINAL = 0x9003, 0x9011
LENS_MAKE, LENS_MODEL = 0xA433, 0xA434
EXPOSURE_TIME, F_NUMBER, ISO = 0x829A, 0x829D, 0x8827
FOCAL_LENGTH, FOCAL_LENGTH_35 = 0x920A, 0xA405

# Bump when read_metadata extracts more fields; unchanged files are then re-read
# (header only) on the next scan without recomputing any derived data.
META_VERSION = 2


@dataclass
class FoundFile:
    source: str
    rel_path: str
    abs_path: Path
    size: int
    mtime: float


@dataclass
class ScanResult:
    images: list[FoundFile] = field(default_factory=list)
    raws: list[FoundFile] = field(default_factory=list)
    added: int = 0
    changed: int = 0
    moved: int = 0
    missing: int = 0
    hidden: int = 0  # in no profile's folders any more (kept for when one adds them back)
    offline: list[tuple[Path, int]] = field(default_factory=list)  # (folder, its photos): unreachable, left as they were
    errors: int = 0
    unchanged: int = 0
    metadata_refreshed: int = 0


def _excluded(path_posix: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path_posix, p) for p in patterns)


def walk_sources(cfg: Config, roots: list[Path] | None = None) -> tuple[list[FoundFile], list[FoundFile]]:
    """Every image and RAW under ``roots`` (default: the config's ``sources``)."""
    images: list[FoundFile] = []
    raws: list[FoundFile] = []
    for source in cfg.sources if roots is None else roots:
        if not source.is_dir():
            log.warning("Source folder not found: %s", source)
            continue
        for dirpath, dirnames, filenames in os.walk(source):
            dir_posix = Path(dirpath).as_posix()
            dirnames[:] = sorted(
                d
                for d in dirnames
                if not _excluded(f"{dir_posix}/{d}/", cfg.exclude)
                and Path(dirpath, d) != cfg.data_dir  # never index our own thumbnails
            )
            for name in sorted(filenames):
                if name.startswith("._"):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in cfg.image_extensions:
                    bucket = images
                elif ext in cfg.raw_extensions:
                    bucket = raws
                else:
                    continue
                abs_path = Path(dirpath) / name
                if _excluded(abs_path.as_posix(), cfg.exclude):
                    continue
                try:
                    st = abs_path.stat()
                except OSError as e:
                    log.warning("Cannot stat %s: %s", abs_path, e)
                    continue
                bucket.append(
                    FoundFile(
                        source=str(source),
                        rel_path=abs_path.relative_to(source).as_posix(),
                        abs_path=abs_path,
                        size=st.st_size,
                        mtime=st.st_mtime,
                    )
                )
    return images, raws


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _clean(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    value = str(value).replace("\x00", "").strip()
    return value or None


def _dms_to_deg(dms, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    deg = d + m / 60 + s / 3600
    if _clean(ref) in ("S", "W"):
        deg = -deg
    return deg


def _number(value) -> float | None:
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    try:
        x = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return x if x > 0 and x == x else None  # drop 0, negatives and NaN (0/0 rationals)


def read_metadata(path: Path) -> dict:
    """Header-level metadata via Pillow. Does not decode pixel data."""
    with Image.open(path) as im:
        width, height = im.size
        exif = im.getexif()
    orientation = exif.get(ORIENTATION, 1)
    if orientation in (5, 6, 7, 8):
        width, height = height, width
    sub = exif.get_ifd(EXIF_IFD)
    gps = exif.get_ifd(GPS_IFD)

    make, model = _clean(exif.get(MAKE)), _clean(exif.get(MODEL))
    if make and model and model.lower().startswith(make.split()[0].lower()):
        camera = model
    else:
        camera = " ".join(x for x in (make, model) if x) or None

    lat = lon = None
    if gps:
        if 2 in gps and 4 in gps:
            lat = _dms_to_deg(gps[2], gps.get(1))
            lon = _dms_to_deg(gps[4], gps.get(3))

    return {
        "width": width,
        "height": height,
        "taken_at": _clean(sub.get(DATETIME_ORIGINAL)) or _clean(exif.get(DATETIME)),
        "tz_offset": _clean(sub.get(OFFSET_TIME_ORIGINAL)),
        "camera": camera,
        "lens": _clean(sub.get(LENS_MODEL)),
        "lat": lat,
        "lon": lon,
        "focal_length": _number(sub.get(FOCAL_LENGTH)),
        "focal_length_35": _number(sub.get(FOCAL_LENGTH_35)),
        "aperture": _number(sub.get(F_NUMBER)),
        "exposure_time": _number(sub.get(EXPOSURE_TIME)),
        "iso": int(n) if (n := _number(sub.get(ISO))) else None,
    }


def _inspect(f: FoundFile) -> tuple[str | None, dict | None, str | None]:
    """Returns (sha256, metadata, error)."""
    try:
        sha = sha256_file(f.abs_path)
    except OSError as e:
        return None, None, f"read failed: {e}"
    try:
        return sha, read_metadata(f.abs_path), None
    except Exception as e:  # noqa: BLE001 - any decode problem is recorded, not fatal
        return sha, None, f"{type(e).__name__}: {e}"


META_COLS = (
    "width", "height", "taken_at", "tz_offset", "camera", "lens", "lat", "lon",
    "focal_length", "focal_length_35", "aperture", "exposure_time", "iso",
)


def invalidate_derived(conn: sqlite3.Connection, cfg: Config, photo_id: int) -> None:
    """Drop everything derived from a photo's content so later steps redo it."""
    for d in (cfg.thumbs_dir, cfg.previews_dir):
        (d / f"{photo_id}.jpg").unlink(missing_ok=True)
    conn.execute(
        """UPDATE photos SET phash = NULL, dupe_group = NULL, sharpness = NULL, stack_id = NULL,
           clip_highlights = NULL, clip_shadows = NULL WHERE id = ?""",
        (photo_id,),
    )
    conn.execute("DELETE FROM embedded WHERE photo_id = ?", (photo_id,))
    conn.execute("DELETE FROM photo_tags WHERE photo_id = ?", (photo_id,))


def _write(conn, f: FoundFile, sha, meta, error, photo_id=None) -> int:
    meta = meta or {}
    values = {
        "source": f.source,
        "rel_path": f.rel_path,
        "sha256": sha or "",
        "size_bytes": f.size,
        "mtime": f.mtime,
        **{c: meta.get(c) for c in META_COLS},
        "meta_version": META_VERSION,
        "status": "error" if error else "ok",
        "error": error,
    }
    if photo_id is None:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cur = conn.execute(f"INSERT INTO photos ({cols}) VALUES ({marks})", list(values.values()))
        return cur.lastrowid
    sets = ", ".join(f"{c} = ?" for c in values)
    conn.execute(f"UPDATE photos SET {sets} WHERE id = ?", [*values.values(), photo_id])
    return photo_id


def scan(conn: sqlite3.Connection, cfg: Config, workers: int = 8, progress=None, roots: list[Path] | None = None) -> ScanResult:
    """Catalogue the files under ``roots`` (every profile's folders: profiles.indexed_roots;
    default the config's). A photo no longer found becomes 'missing' when its folder was
    walked (the file is gone), or 'hidden' when no profile has its folder any more;
    hidden photos keep their derived data (embeddings too), and found again unchanged
    (a folder added back) they are 'ok' at once, without reading the file.

    A folder that cannot be reached (an external drive unplugged, a network share not
    mounted) is *offline*: missing, or empty although photos were catalogued in it (an
    empty mount point). Its photos are left as they were (visible, with all their
    data) and it is reported in ``offline``; a photo of it found elsewhere still counts
    as moved. Settings removes such a folder for good when it was deleted or moved
    (``forget_folder``)."""
    walked = [Path(r) for r in (cfg.sources if roots is None else roots)]
    images, raws = walk_sources(cfg, roots)
    result = ScanResult(images=images, raws=raws)

    existing = {
        (r["source"], r["rel_path"]): r
        for r in conn.execute("SELECT id, source, rel_path, sha256, size_bytes, mtime, status, meta_version FROM photos")
    }
    found_per_root = {str(r): 0 for r in walked}
    for f in images:
        found_per_root[f.source] = found_per_root.get(f.source, 0) + 1
    ok_per_source: dict[str, int] = {}
    for (src, _), row in existing.items():
        if row["status"] == "ok":
            ok_per_source[src] = ok_per_source.get(src, 0) + 1
    offline: list[Path] = []
    for r in walked:
        catalogued = sum(n for src, n in ok_per_source.items() if Path(src) == r or Path(src).is_relative_to(r))
        if catalogued and (not r.is_dir() or not found_per_root.get(str(r))):
            offline.append(r)
            result.offline.append((r, catalogued))
            log.warning("Folder offline, its %d photos are kept as they were: %s", catalogued, r)
    seen = {(f.source, f.rel_path) for f in images}

    todo: list[tuple[FoundFile, sqlite3.Row | None]] = []
    stale_meta: list[tuple[FoundFile, int]] = []
    for f in images:
        row = existing.get((f.source, f.rel_path))
        if row is not None and row["size_bytes"] == f.size and row["mtime"] == f.mtime and row["status"] in ("ok", "missing", "hidden", "error"):
            if row["status"] in ("missing", "hidden"):  # back (its folder added again): the same file, no need to read it
                conn.execute("UPDATE photos SET status = CASE WHEN error IS NULL THEN 'ok' ELSE 'error' END WHERE id = ?", (row["id"],))
            result.unchanged += 1
            if row["status"] == "ok" and row["meta_version"] < META_VERSION:
                stale_meta.append((f, row["id"]))
            continue
        todo.append((f, row))

    # Rows whose path has disappeared: candidates for move detection.
    vanished: dict[str, list[sqlite3.Row]] = {}
    for key, row in existing.items():
        if key not in seen and row["sha256"]:
            vanished.setdefault(row["sha256"], []).append(row)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        inspected = pool.map(lambda item: (item, _inspect(item[0])), todo)
        if progress and todo:
            inspected = progress(inspected, total=len(todo), desc="scan")
        for (f, row), (sha, meta, error) in inspected:
            if error:
                result.errors += 1
                log.warning("%s: %s", f.abs_path, error)
            if row is not None:
                if row["sha256"] != sha:
                    invalidate_derived(conn, cfg, row["id"])
                _write(conn, f, sha, meta, error, row["id"])
                result.changed += 1
            elif sha and vanished.get(sha):
                old = vanished[sha].pop(0)
                _write(conn, f, sha, meta, error, old["id"])
                result.moved += 1
            else:
                _write(conn, f, sha, meta, error)
                result.added += 1

    if stale_meta:
        _refresh_metadata(conn, stale_meta, workers, progress)
        result.metadata_refreshed = len(stale_meta)

    for rows in vanished.values():
        for row in rows:
            src = Path(row["source"])
            if any(src == r or src.is_relative_to(r) for r in offline):
                continue  # its folder is offline: not gone, just not reachable now
            status = "missing" if any(src == r or src.is_relative_to(r) for r in walked) else "hidden"
            if row["status"] != status:
                conn.execute("UPDATE photos SET status = ? WHERE id = ?", (status, row["id"]))
                if status == "missing":
                    result.missing += 1
                else:
                    result.hidden += 1
    conn.commit()
    return result


def _refresh_metadata(conn, items: list[tuple[FoundFile, int]], workers: int, progress=None) -> None:
    """Re-read EXIF for unchanged files catalogued by an older META_VERSION."""

    def read(item):
        f, _ = item
        try:
            return read_metadata(f.abs_path)
        except Exception as e:  # noqa: BLE001 - keep the old metadata
            log.warning("%s: %s", f.abs_path, e)
            return None

    sets = ", ".join(f"{c} = ?" for c in META_COLS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(read, items)
        if progress:
            results = progress(results, total=len(items), desc="metadata")
        for (_, photo_id), meta in zip(items, results):
            if meta is not None:
                conn.execute(
                    f"UPDATE photos SET {sets}, meta_version = ? WHERE id = ?",
                    [*(meta.get(c) for c in META_COLS), META_VERSION, photo_id],
                )


def forget_folder(conn: sqlite3.Connection, folder: Path) -> int:
    """The photos catalogued in ``folder`` are gone for good (deleted, or moved and added
    from their new place): mark them 'missing', so indexing drops their embeddings and a
    clean-up their other data. Returns how many."""
    folder = Path(folder)
    rows = conn.execute("SELECT id, source, rel_path FROM photos WHERE status != 'missing'").fetchall()
    gone = [r["id"] for r in rows if (Path(r["source"]) / r["rel_path"]).is_relative_to(folder)]
    with conn:
        conn.executemany("UPDATE photos SET status = 'missing' WHERE id = ?", [(i,) for i in gone])
    return len(gone)
