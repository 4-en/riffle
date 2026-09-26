"""Copy picked photos (and/or their RAWs) into a new folder.

Originals are only read. Nothing in the destination is overwritten: a file that
is already there with the same size and modification time is skipped (so an
interrupted export can be resumed), any other name clash gets a ``-1``, ``-2``…
suffix, the same one for a photo's image and RAW so they still pair up.

With ``add_location``, photos without camera GPS that were placed from the
location history get their position written into the *copies* (see geotag.py):
into the metadata of JPEG and PNG copies, or as an XMP sidecar for RAW and
other formats.
"""

from __future__ import annotations

import csv
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .geotag import Tagged, tag, xmp_packet
from .locate import TIMELINE_SOURCES

CONTENT = ("images", "images_raws", "raws")
STRUCTURE = ("flat", "folders")


@dataclass
class _File:
    photo_id: int
    kind: str  # image | raw | sidecar
    src: Path  # for a sidecar: the name it takes (the paired file with .xmp)
    size: int
    rel_dir: Path  # its folder relative to the sources' parent, e.g. "Trip/RAW"
    target: Path | None = None
    status: str = ""  # copied | skipped
    tagged: Tagged | None = None  # new metadata head for a geotagged copy
    content: bytes | None = None  # a sidecar's content
    location: str = ""  # exif | xmp | sidecar: how the position was added

    @property
    def out_size(self) -> int:
        if self.content is not None:
            return len(self.content)
        if self.tagged is not None:
            return len(self.tagged.head) + self.size - self.tagged.rest
        return self.size

    def already_at(self, target: Path) -> bool:
        """The destination file is what this export would write (resume)."""
        try:
            st = target.stat()
        except OSError:
            return False
        if self.content is not None:
            return st.st_size == len(self.content) and target.read_bytes() == self.content
        try:
            src_mtime = self.src.stat().st_mtime
        except OSError:
            return False
        return st.st_size == self.out_size and int(st.st_mtime) == int(src_mtime)


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
        raise ExportError("the destination is inside Riffle's data folder")
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


def add_locations(conn: sqlite3.Connection, files: list[_File]) -> None:
    """Geotag copies of photos placed from the location history (not camera GPS):
    JPEG/PNG copies get it in their metadata, other files an XMP sidecar."""
    ids = sorted({f.photo_id for f in files})
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    where = ",".join("?" * len(TIMELINE_SOURCES))
    locations = {
        r[0]: (r[1], r[2], r[3])
        for r in conn.execute(
            f"""SELECT l.photo_id, l.lat, l.lon, l.accuracy_m FROM photo_locations l
                JOIN photos p ON p.id = l.photo_id
                WHERE l.photo_id IN ({marks}) AND l.source IN ({where}) AND p.lat IS NULL""",
            [*ids, *TIMELINE_SOURCES],
        )
    }
    sidecars: list[_File] = []
    seen: set[tuple[int, Path, str]] = set()
    for f in files:
        loc = locations.get(f.photo_id)
        if loc is None:
            continue
        tagged = tag(f.src, *loc) if f.kind == "image" else None
        if tagged is not None:
            f.tagged, f.location = tagged, tagged.method
            continue
        key = (f.photo_id, f.rel_dir, f.src.stem)
        if key in seen:
            continue
        seen.add(key)
        content = xmp_packet(*loc)
        sidecars.append(_File(f.photo_id, "sidecar", f.src.with_suffix(".xmp"), len(content), f.rel_dir, content=content, location="sidecar"))
    files.extend(sidecars)


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
                t not in claimed and (not t.exists() or f.already_at(t))
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
    add_location: bool = False,
) -> dict:
    """Copy the files. Runs as a BackgroundJob; returns the summary."""
    from . import db

    dest = Path(folder)
    check_destination(cfg, dest)
    conn = db.connect(cfg.db_path)
    try:
        files, without_raw = plan_files(conn, photo_ids, content, raw_fallback)
        if add_location:
            add_locations(conn, files)
    finally:
        conn.close()

    dest.mkdir(parents=True, exist_ok=True)
    assign_targets(files, dest, structure)
    todo = [f for f in files if not f.target.exists()]
    for f in files:
        if f.target.exists():
            f.status = "skipped"
    needed = sum(f.out_size for f in todo)
    free = shutil.disk_usage(dest).free
    if needed > free:
        raise ExportError(f"not enough space: need {needed / 1e9:.2f} GB, {free / 1e9:.2f} GB free")
    report(f"copying {len(todo)} files ({needed / 1e6:.0f} MB) to {dest}")

    bar = progress(total=len(todo), desc="copying") if progress else None
    for f in todo:
        f.target.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.target.with_name(f".{f.target.name}.partial")
        if f.content is not None:
            tmp.write_bytes(f.content)
        elif f.tagged is not None:
            with open(f.src, "rb") as src, open(tmp, "wb") as out:
                out.write(f.tagged.head)
                src.seek(f.tagged.rest)
                shutil.copyfileobj(src, out, 1 << 20)
            st = f.src.stat()
            os.utime(tmp, (st.st_atime, st.st_mtime))  # keep the capture file's date, like copy2
        else:
            shutil.copy2(f.src, tmp)
        os.replace(tmp, f.target)
        f.status = "copied"
        if bar:
            bar.update(1)

    # Remember which photos are now in an export folder (copied now or already there).
    from . import selections

    exported_ids = sorted({f.photo_id for f in files if f.status in ("copied", "skipped") and f.kind != "sidecar"})
    conn = db.connect(cfg.db_path)
    try:
        marked = selections.mark_exported(conn, cfg.selections_path, exported_ids, str(dest)) if exported_ids else 0
    finally:
        conn.close()

    manifest = _unique(dest / "export-manifest.csv")
    with open(manifest, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["photo_id", "kind", "source", "exported", "status", "location"])
        for f in files:
            src = "" if f.kind == "sidecar" else str(f.src)
            w.writerow([f.photo_id, f.kind, src, str(f.target), f.status, f.location])

    summary = {
        "marked": marked,
        "folder": str(dest),
        "photos": len(photo_ids),
        "copied": sum(f.status == "copied" for f in files),
        "skipped": sum(f.status == "skipped" for f in files),
        "bytes": needed,
        "without_raw": without_raw if content != "images" else 0,
        "geotagged": len({f.photo_id for f in files if f.location}),
        "sidecars": sum(f.kind == "sidecar" for f in files),
        "manifest": str(manifest),
    }
    report(
        f"exported {summary['photos']} photos: {summary['copied']} files copied, "
        f"{summary['skipped']} already there"
        + (f", {summary['without_raw']} without RAW" if summary["without_raw"] else "")
        + (f", location added to {summary['geotagged']}" if summary["geotagged"] else "")
    )
    return summary
