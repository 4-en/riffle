"""Copy picked photos (and/or their RAWs) into a new folder.

Originals are only read. Nothing in the destination is overwritten: a file that
is already there with the same size and modification time is skipped (so an
interrupted export can be resumed), any other name clash gets a ``-1``, ``-2``…
suffix, the same one for a photo's image and RAW so they still pair up.
"""

from __future__ import annotations

import csv
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import Config

CONTENT = ("images", "images_raws", "raws")
STRUCTURE = ("flat", "folders")


@dataclass
class _File:
    photo_id: int
    kind: str  # image | raw
    src: Path
    size: int
    rel_dir: Path  # its folder relative to the sources' parent, e.g. "Trip/RAW"
    target: Path | None = None
    status: str = ""  # copied | skipped


class ExportError(ValueError):
    pass


def check_destination(cfg: Config, folder: Path) -> None:
    """Refuse destinations that would be indexed (sources) or are derived data."""
    if not folder.is_absolute():
        raise ExportError("use an absolute destination path")
    for s in cfg.sources:
        if folder == s or folder.is_relative_to(s):
            raise ExportError(f"the destination is inside the photo folder {s}; its copies would be indexed again")
    if folder == cfg.data_dir or folder.is_relative_to(cfg.data_dir):
        raise ExportError("the destination is inside the archive's data folder")
    if folder.exists() and not folder.is_dir():
        raise ExportError(f"not a folder: {folder}")


def plan_files(
    conn: sqlite3.Connection, photo_ids: list[int], content: str, raw_fallback: bool
) -> tuple[list[_File], int]:
    """Files to copy, grouped by photo in the given order, and the number of
    photos that have no RAW (and were skipped or fell back to the image)."""
    if content not in CONTENT:
        raise ExportError(f"content must be one of {', '.join(CONTENT)}")
    files: list[_File] = []
    without_raw = 0
    for pid in photo_ids:
        p = conn.execute("SELECT source, rel_path FROM photos WHERE id = ?", (pid,)).fetchone()
        if p is None:
            continue
        image = (Path(p["source"]) / p["rel_path"], Path(Path(p["source"]).name) / Path(p["rel_path"]).parent)
        raws = [
            (Path(r["source"]) / r["rel_path"], Path(Path(r["source"]).name) / Path(r["rel_path"]).parent)
            for r in conn.execute(
                "SELECT source, rel_path FROM raws WHERE photo_id = ? ORDER BY rel_path", (pid,)
            )
        ]
        wanted: list[tuple[str, tuple[Path, Path]]] = []
        if content in ("images", "images_raws"):
            wanted.append(("image", image))
        if content in ("images_raws", "raws"):
            wanted += [("raw", r) for r in raws]
            if not raws:
                without_raw += 1
                if content == "raws" and raw_fallback:
                    wanted.append(("image", image))
        for kind, (src, rel_dir) in wanted:
            try:
                size = src.stat().st_size
            except OSError:
                continue  # file vanished since indexing
            files.append(_File(pid, kind, src, size, rel_dir))
    return files, without_raw


def _same_file(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)


def assign_targets(files: list[_File], folder: Path, structure: str) -> None:
    """Choose each file's destination path (see module docstring). ``folders``
    mirrors each file's location under its source folder's name."""
    if structure not in STRUCTURE:
        raise ExportError(f"structure must be one of {', '.join(STRUCTURE)}")
    claimed: set[Path] = set()
    by_photo: dict[int, list[_File]] = {}
    for f in files:
        by_photo.setdefault(f.photo_id, []).append(f)
    for group in by_photo.values():
        n = 0
        while True:
            suffix = f"-{n}" if n else ""
            targets = [
                (folder / f.rel_dir if structure == "folders" else folder) / f"{f.src.stem}{suffix}{f.src.suffix}"
                for f in group
            ]
            fits = all(
                t not in claimed and (not t.exists() or _same_file(f.src, t))
                for f, t in zip(group, targets)
            )
            if fits and len(set(targets)) == len(targets):
                break
            n += 1
        for f, t in zip(group, targets):
            f.target = t
            claimed.add(t)


def _unique(path: Path) -> Path:
    n = 0
    while True:
        candidate = path.with_name(f"{path.stem}{f'-{n}' if n else ''}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def run_export(
    cfg: Config,
    progress=None,
    report=print,
    *,
    photo_ids: list[int],
    folder: str,
    content: str = "images",
    raw_fallback: bool = True,
    structure: str = "flat",
) -> dict:
    """Copy the files. Runs as a BackgroundJob; returns the summary."""
    from . import db

    dest = Path(folder)
    check_destination(cfg, dest)
    conn = db.connect(cfg.db_path)
    try:
        files, without_raw = plan_files(conn, photo_ids, content, raw_fallback)
    finally:
        conn.close()

    dest.mkdir(parents=True, exist_ok=True)
    assign_targets(files, dest, structure)
    todo = [f for f in files if not f.target.exists()]
    for f in files:
        if f.target.exists():
            f.status = "skipped"
    needed = sum(f.size for f in todo)
    free = shutil.disk_usage(dest).free
    if needed > free:
        raise ExportError(f"not enough space: need {needed / 1e9:.2f} GB, {free / 1e9:.2f} GB free")
    report(f"copying {len(todo)} files ({needed / 1e6:.0f} MB) to {dest}")

    bar = progress(total=len(todo), desc="copying") if progress else None
    for f in todo:
        f.target.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.target.with_name(f".{f.target.name}.partial")
        shutil.copy2(f.src, tmp)
        os.replace(tmp, f.target)
        f.status = "copied"
        if bar:
            bar.update(1)

    manifest = _unique(dest / "export-manifest.csv")
    with open(manifest, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["photo_id", "kind", "source", "exported", "status"])
        for f in files:
            w.writerow([f.photo_id, f.kind, str(f.src), str(f.target), f.status])

    summary = {
        "folder": str(dest),
        "photos": len(photo_ids),
        "copied": sum(f.status == "copied" for f in files),
        "skipped": sum(f.status == "skipped" for f in files),
        "bytes": needed,
        "without_raw": without_raw if content != "images" else 0,
        "manifest": str(manifest),
    }
    report(
        f"exported {summary['photos']} photos: {summary['copied']} files copied, "
        f"{summary['skipped']} already there"
        + (f", {summary['without_raw']} without RAW" if summary["without_raw"] else "")
    )
    return summary
