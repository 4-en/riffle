"""Non-destructive edits: an edit list per photo, rendered on top of the untouched original.

Edits are the user's work, so they live with the user data (next to the flags, in their
own file because every profile shares them): ``edits.sqlite3`` and the pixel assets in
``edits/<sha256>/``. They are keyed by file content, like the flags: a moved file keeps
them; a file changed outside Riffle (new content) leaves them behind.

Kinds:

- ``geometry`` (at most one per photo): quarter turns, straightening (degrees), a crop
  as fractions of the turned and straightened frame, a horizontal flip;
- ``heal`` / ``remove`` / ``inpaint`` / ``instruct``: a patch of pixels with its mask, over a box in
  the upright original's pixels, plus how it was made (tool, model, prompt, seed…).

Retouching lives in the original's pixel space and the geometry is applied last, so
disabling or deleting any step never shifts another. AI results are kept as pixels and
never re-run: diffusion is not reproducible across versions and machines. Each edit
records the original's size, so a render at any size (a preview) only scales.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from .images import open_image

KINDS = ("geometry", "heal", "remove", "inpaint", "instruct")
RETOUCH = ("heal", "remove", "inpaint", "instruct")

SCHEMA = """
CREATE TABLE IF NOT EXISTS edits (
  id          INTEGER PRIMARY KEY,
  sha256      TEXT NOT NULL,
  seq         INTEGER NOT NULL,          -- order within the photo
  kind        TEXT NOT NULL,
  params      TEXT NOT NULL,             -- JSON
  enabled     INTEGER NOT NULL DEFAULT 1,
  source      TEXT,                      -- last known location, for humans and recovery
  rel_path    TEXT,
  created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS edits_sha ON edits(sha256);
"""


def store_path(selections_path: str | Path) -> Path:
    """The edits file: next to the (default profile's) flags, shared by all profiles."""
    return Path(selections_path).parent / "edits.sqlite3"


def assets_dir(selections_path: str | Path) -> Path:
    return Path(selections_path).parent / "edits"


def connect(selections_path: str | Path) -> sqlite3.Connection:
    path = store_path(selections_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


@dataclass
class Edit:
    id: int
    sha256: str
    seq: int
    kind: str
    params: dict
    enabled: bool
    created_at: float = 0.0
    folder: Path | None = field(default=None, repr=False)  # where its assets are

    @property
    def mask_path(self) -> Path:
        return self.folder / f"{self.id}.mask.png"

    @property
    def patch_path(self) -> Path:
        return self.folder / f"{self.id}.patch.png"

    def as_dict(self) -> dict:
        return {"id": self.id, "seq": self.seq, "kind": self.kind, "params": self.params,
                "enabled": self.enabled, "created_at": self.created_at}


def _row(r, base: Path) -> Edit:
    return Edit(r["id"], r["sha256"], r["seq"], r["kind"], json.loads(r["params"]), bool(r["enabled"]),
                r["created_at"], base / r["sha256"])


def list_edits(selections_path, sha256: str) -> list[Edit]:
    conn = connect(selections_path)
    try:
        base = assets_dir(selections_path)
        return [_row(r, base) for r in conn.execute("SELECT * FROM edits WHERE sha256 = ? ORDER BY seq, id", (sha256,))]
    finally:
        conn.close()


def edited_shas(selections_path) -> set[str]:
    """Content hashes of the photos with at least one enabled edit."""
    if not store_path(selections_path).exists():
        return set()
    conn = connect(selections_path)
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT sha256 FROM edits WHERE enabled = 1")}
    finally:
        conn.close()


def add_edit(
    selections_path, sha256: str, kind: str, params: dict,
    mask: Image.Image | None = None, patch: Image.Image | None = None,
    source: str | None = None, rel_path: str | None = None,
) -> int:
    """Add an edit at the end of the photo's list. A geometry edit replaces the photo's
    previous one (there is one frame). Returns its id."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if kind in RETOUCH and (mask is None or patch is None):
        raise ValueError("a retouch needs its mask and patch")
    conn = connect(selections_path)
    try:
        with conn:
            if kind == "geometry":
                for r in conn.execute("SELECT id FROM edits WHERE sha256 = ? AND kind = 'geometry'", (sha256,)).fetchall():
                    _delete(conn, selections_path, r["id"], sha256)
            seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM edits WHERE sha256 = ?", (sha256,)).fetchone()[0]
            edit_id = conn.execute(
                "INSERT INTO edits (sha256, seq, kind, params, enabled, source, rel_path, created_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                (sha256, seq, kind, json.dumps(params), source, rel_path, time.time()),
            ).lastrowid
        if kind in RETOUCH:
            folder = assets_dir(selections_path) / sha256
            folder.mkdir(parents=True, exist_ok=True)
            mask.convert("L").save(folder / f"{edit_id}.mask.png")
            patch.convert("RGB").save(folder / f"{edit_id}.patch.png")
        return edit_id
    finally:
        conn.close()


def _delete(conn, selections_path, edit_id: int, sha256: str) -> None:
    conn.execute("DELETE FROM edits WHERE id = ?", (edit_id,))
    folder = assets_dir(selections_path) / sha256
    for name in (f"{edit_id}.mask.png", f"{edit_id}.patch.png"):
        (folder / name).unlink(missing_ok=True)


def edit_sha(selections_path, edit_id: int) -> str | None:
    conn = connect(selections_path)
    try:
        r = conn.execute("SELECT sha256 FROM edits WHERE id = ?", (edit_id,)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def set_enabled(selections_path, edit_id: int, enabled: bool) -> None:
    conn = connect(selections_path)
    try:
        with conn:
            conn.execute("UPDATE edits SET enabled = ? WHERE id = ?", (int(enabled), edit_id))
    finally:
        conn.close()


def delete_edit(selections_path, edit_id: int) -> None:
    conn = connect(selections_path)
    try:
        with conn:
            r = conn.execute("SELECT sha256 FROM edits WHERE id = ?", (edit_id,)).fetchone()
            if r:
                _delete(conn, selections_path, edit_id, r[0])
    finally:
        conn.close()


def restore(selections_path, sha256: str) -> int:
    """Back to the original: every edit disabled (kept, to turn back on). Returns how many."""
    conn = connect(selections_path)
    try:
        with conn:
            return conn.execute("UPDATE edits SET enabled = 0 WHERE sha256 = ? AND enabled = 1", (sha256,)).rowcount
    finally:
        conn.close()


def forget(selections_path, sha256: str) -> None:
    """Delete a photo's edits and their assets (after baking them in)."""
    conn = connect(selections_path)
    try:
        with conn:
            conn.execute("DELETE FROM edits WHERE sha256 = ?", (sha256,))
    finally:
        conn.close()
    shutil.rmtree(assets_dir(selections_path) / sha256, ignore_errors=True)


# ---- rendering ------------------------------------------------------------------------


def apply_retouch(im: Image.Image, edits: list[Edit], original_size: tuple[int, int]) -> Image.Image:
    """Paste the enabled patches (each through its mask) onto ``im``, which is the upright
    original at any scale."""
    sx = im.width / original_size[0]
    sy = im.height / original_size[1]
    for e in edits:
        if not e.enabled or e.kind not in RETOUCH:
            continue
        x, y, w, h = e.params["bbox"]
        with Image.open(e.patch_path) as p, Image.open(e.mask_path) as m:
            box = (round(x * sx), round(y * sy), round((x + w) * sx), round((y + h) * sy))
            size = (max(1, box[2] - box[0]), max(1, box[3] - box[1]))
            patch = p.convert("RGB").resize(size, Image.Resampling.LANCZOS)
            mask = m.convert("L").resize(size, Image.Resampling.BILINEAR)
            im.paste(patch, box[:2], mask)
    return im


def apply_geometry(im: Image.Image, params: dict) -> Image.Image:
    """Quarter turns, straightening, crop (fractions of the resulting frame), flip."""
    k = int(params.get("rot90", 0)) % 4
    if k:
        im = im.transpose((None, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_90)[k])
    angle = float(params.get("angle", 0.0))
    if angle:
        # Clockwise for a positive angle, like dragging a straighten slider to the right.
        im = im.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(0, 0, 0))
    crop = params.get("crop")
    if crop:
        fx, fy, fw, fh = crop
        box = (round(fx * im.width), round(fy * im.height), round((fx + fw) * im.width), round((fy + fh) * im.height))
        if box[2] > box[0] and box[3] > box[1]:
            im = im.crop(box)
    if params.get("flip_h"):
        im = ImageOps.mirror(im)
    return im


def render(path: Path, edits: list[Edit], max_edge: int | None = None, geometry: bool = True) -> Image.Image:
    """The photo with its enabled edits: retouching on the upright original, then the
    geometry. ``geometry=False``: the retouched original (what a new retouch works on)."""
    im = open_image(path, max_edge=max_edge)
    if max_edge and max(im.size) > max_edge:
        im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    original_size = _original_size(path, edits)
    im = apply_retouch(im, edits, original_size)
    if geometry:
        for e in edits:
            if e.enabled and e.kind == "geometry":
                im = apply_geometry(im, e.params)
    return im


def _original_size(path: Path, edits: list[Edit]) -> tuple[int, int]:
    for e in edits:
        if e.params.get("size"):
            return tuple(e.params["size"])
    with Image.open(path) as im:
        w, h = im.size
        if (im.getexif().get(0x0112) or 1) in (5, 6, 7, 8):
            w, h = h, w
    return w, h


def upright_size(path: Path) -> tuple[int, int]:
    """The original's size once turned upright (edit coordinates are in it)."""
    return _original_size(path, [])


def versions(selections_path) -> dict[str, str]:
    """{sha256: a short version of its enabled edits}, for photos with any (so a changed
    edit list gives the thumbnail a new URL)."""
    if not store_path(selections_path).exists():
        return {}
    conn = connect(selections_path)
    try:
        return {
            r[0]: f"{r[1]}-{int(r[2] * 1000) % 100000000}"
            for r in conn.execute("SELECT sha256, COUNT(*), MAX(created_at) FROM edits WHERE enabled = 1 GROUP BY sha256")
        }
    finally:
        conn.close()


# Derived measures that follow the pixels: redone for the edited version by the next index run.
DERIVED = ("phash", "sharpness", "clip_highlights", "clip_shadows", "brightness", "contrast",
           "colorfulness", "hues", "layout", "accents")


def render_derivatives(conn: sqlite3.Connection, cfg, photo_id: int) -> None:
    """Re-render a photo's thumbnail and preview through its edit list (no catalogue write)."""
    from .thumbs import make_derivatives

    r = conn.execute("SELECT id, source, rel_path, sha256 FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if r is None:
        raise ValueError("no such photo")
    cfg.thumbs_dir.mkdir(parents=True, exist_ok=True)
    cfg.previews_dir.mkdir(parents=True, exist_ok=True)
    make_derivatives(
        Path(r["source"]) / r["rel_path"], cfg.thumbs_dir / f"{photo_id}.jpg", cfg.previews_dir / f"{photo_id}.jpg",
        list_edits(cfg.selections_path, r["sha256"]),
    )


def invalidate(conn: sqlite3.Connection, photo_ids) -> None:
    """Clear the photos' measures, embeddings and tags for the next index run to redo."""
    ids = [(int(i),) for i in photo_ids]
    with conn:
        conn.executemany(f"UPDATE photos SET {', '.join(f'{c} = NULL' for c in DERIVED)} WHERE id = ?", ids)
        conn.executemany("DELETE FROM embedded WHERE photo_id = ?", ids)
        conn.executemany("DELETE FROM photo_tags WHERE photo_id = ?", ids)


def apply_to_library(conn: sqlite3.Connection, cfg, photo_id: int) -> None:
    """After a photo's edit list changed: its thumbnail and preview re-rendered now, its
    measures, embedding and tags cleared for the next index run to redo from them."""
    render_derivatives(conn, cfg, photo_id)
    invalidate(conn, [photo_id])


# ---- baking edits into the file -------------------------------------------------------

def backup_dir(selections_path) -> Path:
    return Path(selections_path).parent / "originals-backup"


def _save_like(im: Image.Image, src: Path, dest: Path) -> None:
    """Save ``im`` in the format of ``src``, keeping its EXIF (upright now: orientation 1)
    and tagging the sRGB it was rendered in."""
    from PIL import ImageCms

    with Image.open(src) as orig:
        fmt = orig.format or "JPEG"
        exif = orig.getexif()
    exif[0x0112] = 1
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    kwargs: dict = {"icc_profile": srgb}
    if fmt == "JPEG":
        kwargs |= {"quality": 95, "subsampling": 0, "exif": exif.tobytes()}
    elif fmt in ("PNG", "TIFF", "WEBP"):
        kwargs["exif"] = exif.tobytes()
    im.save(dest, fmt, **kwargs)


def bake(conn: sqlite3.Connection, cfg, photo_id: int, backup: bool = True) -> dict:
    """Write the photo's edits into its file. The original goes to the backup folder
    (unless ``backup`` is False); the file's new content hash takes over everything
    recorded for the old one in every profile (flags, export history, tag examples,
    captions…); the edit list is done with. Returns {sha256, backup}."""
    from . import profiles, selections
    from .scan import sha256_file

    r = conn.execute("SELECT id, source, rel_path, sha256 FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if r is None:
        raise ValueError("no such photo")
    path = Path(r["source"]) / r["rel_path"]
    old = r["sha256"]
    edit_list = list_edits(cfg.selections_path, old)
    if not any(e.enabled for e in edit_list):
        raise ValueError("the photo has no edits to bake in")
    im = render(path, edit_list)
    tmp = path.with_name(f".{path.name}.riffle-bake{path.suffix}")
    _save_like(im, path, tmp)
    kept = None
    if backup:
        kept = backup_dir(cfg.selections_path) / time.strftime("%Y%m%d-%H%M%S") / Path(r["source"]).name / r["rel_path"]
        kept.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, kept)  # (a copy: the file may be on another drive)
    tmp.replace(path)
    new = sha256_file(path)
    st = path.stat()
    for slug in profiles.slugs(cfg):
        selections.migrate_sha(profiles.path_for(cfg, slug), old, new)
    forget(cfg.selections_path, old)
    with conn:
        conn.execute(
            "UPDATE photos SET sha256 = ?, size_bytes = ?, mtime = ?, width = ?, height = ? WHERE id = ?",
            (new, st.st_size, st.st_mtime, im.width, im.height, photo_id),
        )
    apply_to_library(conn, cfg, photo_id)
    return {"sha256": new, "backup": str(kept) if kept else None}
