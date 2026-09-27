"""Copy picked photos (and/or their RAWs) into a new folder.

Originals are only read. Nothing in the destination is overwritten: a file that
is already there with the same size and modification time is skipped (so an
interrupted export can be resumed), any other name clash gets a ``-1``, ``-2``…
suffix, the same one for a photo's image and RAW so they still pair up.

With ``add_location``, photos without camera GPS that were placed from the
location history get their position written into the *copies* (see geotag.py):
into the metadata of JPEG and PNG copies, or as an XMP sidecar for RAW and
other formats.

With ``captions``, each photo's caption and fixed tags (selections.py) go along:
``embed`` into the copies' XMP (a sidecar where that is not possible), ``xmp`` as
sidecars, ``txt`` as a text file with the image's name, or ``jsonl`` as one
``metadata.jsonl`` (the Hugging Face imagefolder format). Photos without either
get nothing.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .geotag import Meta, Tagged, tag, xmp_packet
from .locate import TIMELINE_SOURCES

CONTENT = ("images", "images_raws", "raws")
STRUCTURE = ("flat", "folders")
CAPTIONS = ("embed", "xmp", "txt", "jsonl")
CAPTION_TEXT = ("tags", "caption", "both")


@dataclass
class _File:
    photo_id: int
    kind: str  # image | raw | sidecar (XMP) | caption (text file)
    src: Path  # for a sidecar: the name it takes (the paired file with .xmp)
    size: int
    rel_dir: Path  # its folder relative to the sources' parent, e.g. "Trip/RAW"
    target: Path | None = None
    status: str = ""  # copied | skipped
    tagged: Tagged | None = None  # new metadata head for a geotagged copy
    content: bytes | None = None  # a sidecar's content
    location: str = ""  # exif | xmp | sidecar: how the position was added
    caption: str = ""  # xmp | sidecar | txt: how the caption and tags were added

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


def timeline_locations(conn: sqlite3.Connection, ids: list[int]) -> dict[int, tuple]:
    """{photo id: (lat, lon, accuracy)} for photos placed from the location history
    (not camera GPS): the positions an export may add."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    where = ",".join("?" * len(TIMELINE_SOURCES))
    return {
        r[0]: (r[1], r[2], r[3])
        for r in conn.execute(
            f"""SELECT l.photo_id, l.lat, l.lon, l.accuracy_m FROM photo_locations l
                JOIN photos p ON p.id = l.photo_id
                WHERE l.photo_id IN ({marks}) AND l.source IN ({where}) AND p.lat IS NULL""",
            [*ids, *TIMELINE_SOURCES],
        )
    }


def add_metadata(files: list[_File], locations: dict[int, tuple], texts: dict[int, tuple[str, list[str]]], embed_text: bool) -> None:
    """Positions (``locations``) and captions/keywords (``texts``: {id: (caption,
    keywords)}) for the copies: into JPEG/PNG copies where possible (captions only
    with ``embed_text``), else one XMP sidecar per photo and name."""
    sidecars: list[_File] = []
    seen: set[tuple[int, Path, str]] = set()
    for f in list(files):
        loc = locations.get(f.photo_id)
        caption, keywords = texts.get(f.photo_id, ("", []))
        if loc is None and not (caption or keywords):
            continue
        embed = Meta(*(loc or (None, None, None)), *((caption, tuple(keywords)) if embed_text else ("", ())))
        tagged = tag(f.src, meta=embed) if f.kind == "image" and (embed.has_gps or embed.has_text) else None
        if tagged is not None:
            f.tagged = tagged
            f.location = tagged.method if loc else ""
            f.caption = "xmp" if embed.has_text else ""
            if embed_text or not (caption or keywords):
                continue
            loc = None  # the position is in the copy; the caption still needs a sidecar
        key = (f.photo_id, f.rel_dir, f.src.stem)
        if key in seen:
            continue
        seen.add(key)
        content = xmp_packet(*(loc or (None, None, None)), description=caption, keywords=keywords)
        side = _File(f.photo_id, "sidecar", f.src.with_suffix(".xmp"), len(content), f.rel_dir, content=content)
        side.location = "sidecar" if loc else ""
        side.caption = "sidecar" if (caption or keywords) else ""
        sidecars.append(side)
    files.extend(sidecars)


def caption_text(caption: str, tags: list[str], what: str, underscores: bool) -> str:
    """The text of a caption file: tags (comma-separated), the caption, or both
    (the caption, then the tags on the next line)."""
    tags = [t.replace("_", " ") for t in tags] if underscores else tags
    parts = {"tags": [", ".join(tags)], "caption": [caption], "both": [caption, ", ".join(tags)]}[what]
    return "\n".join(p for p in parts if p)


def add_caption_files(files: list[_File], texts: dict[int, tuple[str, list[str]]], what: str, underscores: bool) -> None:
    """A text file per photo, named like its first file (the image, or the RAW)."""
    first: dict[int, _File] = {}
    for f in files:
        if f.kind in ("image", "raw"):
            first.setdefault(f.photo_id, f)
    for pid, f in first.items():
        caption, tags = texts.get(pid, ("", []))
        text = caption_text(caption, tags, what, underscores)
        if text:
            content = (text + "\n").encode()
            side = _File(pid, "caption", f.src.with_suffix(".txt"), len(content), f.rel_dir, content=content)
            side.caption = "txt"
            files.append(side)


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
    selections_path: str | Path | None = None,
    captions: str | None = None,
    caption_text: str = "tags",
    underscores: bool = False,
) -> dict:
    """Copy the files. Runs as a BackgroundJob; returns the summary. The export
    history goes to ``selections_path`` (the profile active when it started),
    else the config's."""
    from . import db

    from . import selections

    if captions is not None and captions not in CAPTIONS:
        raise ExportError(f"captions must be one of {', '.join(CAPTIONS)}")
    if caption_text not in CAPTION_TEXT:
        raise ExportError(f"caption_text must be one of {', '.join(CAPTION_TEXT)}")
    dest = Path(folder)
    check_destination(cfg, dest)
    sel = selections_path or cfg.selections_path
    conn = db.connect(cfg.db_path)
    try:
        files, without_raw = plan_files(conn, photo_ids, content, raw_fallback)
        texts: dict[int, tuple[str, list[str]]] = {}
        if captions:
            found = selections.captions_for(conn, sel, photo_ids)
            texts = {pid: (c["caption"] or "", c["tags"]) for pid, c in found.items() if c["caption"] or c["tags"]}
        locations = timeline_locations(conn, sorted({f.photo_id for f in files})) if add_location else {}
        add_metadata(files, locations, texts if captions in ("embed", "xmp") else {}, embed_text=captions == "embed")
        if captions == "txt":
            add_caption_files(files, texts, caption_text, underscores)
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
    exported_ids = sorted({f.photo_id for f in files if f.status in ("copied", "skipped") and f.kind in ("image", "raw")})
    conn = db.connect(cfg.db_path)
    try:
        marked = selections.mark_exported(conn, selections_path or cfg.selections_path, exported_ids, str(dest)) if exported_ids else 0
    finally:
        conn.close()

    metadata = None
    if captions == "jsonl" and texts:
        # Hugging Face imagefolder: file_name relative to the folder, plus the text.
        metadata = _unique(dest / "metadata.jsonl")
        with open(metadata, "w", encoding="utf-8") as fh:
            done: set[int] = set()
            for f in files:
                if f.kind not in ("image", "raw") or f.photo_id in done or f.photo_id not in texts:
                    continue
                done.add(f.photo_id)
                caption, tags = texts[f.photo_id]
                tags = [t.replace("_", " ") for t in tags] if underscores else tags
                row = {"file_name": f.target.relative_to(dest).as_posix(), "text": caption or ", ".join(tags), "tags": tags}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.caption = "jsonl"

    manifest = _unique(dest / "export-manifest.csv")
    with open(manifest, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["photo_id", "kind", "source", "exported", "status", "location", "caption"])
        for f in files:
            src = "" if f.kind in ("sidecar", "caption") else str(f.src)
            w.writerow([f.photo_id, f.kind, src, str(f.target), f.status, f.location, f.caption])

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
        "captioned": len({f.photo_id for f in files if f.caption}),
        "without_caption": len(set(photo_ids) - set(texts)) if captions else 0,
        "metadata": str(metadata) if metadata else None,
        "manifest": str(manifest),
    }
    report(
        f"exported {summary['photos']} photos: {summary['copied']} files copied, "
        f"{summary['skipped']} already there"
        + (f", {summary['without_raw']} without RAW" if summary["without_raw"] else "")
        + (f", location added to {summary['geotagged']}" if summary["geotagged"] else "")
        + (f", captions and tags for {summary['captioned']}" if summary["captioned"] else "")
    )
    return summary
