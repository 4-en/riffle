"""Reading captions, tags and read text back from the formats export writes.

When a folder is added (and on request, per folder in Settings), each photo in it is
checked for, in this order:

1. ``metadata.jsonl`` (Hugging Face imagefolder) in the photo's folder or one above it:
   ``text`` (the caption, unless it is only the tags joined), ``tags``, ``ocr_text``,
   ``ocr_translation``;
2. an XMP sidecar, ``<name>.xmp`` or ``<name>.<ext>.xmp`` (also darktable's and
   Lightroom's: their keywords and descriptions come along, darktable's automatic
   ``darktable|…`` tags do not);
3. XMP inside the JPEG or PNG;
4. a text file ``<name>.txt``.

XMP gives the caption (``dc:description``), the tags (``dc:subject``) and, from
Riffle's own fields, the read text and its translation; export also appends those to
the description for photo apps, and the import takes them off again. A text file
holds tags (comma-separated), a caption, or the caption and then the tags on the next
line. Read text follows them there with nothing to mark it, so it is not taken from
text files (lines after the caption and tags are left out).

The first format with something for a part (caption, tags, read text) gives that part.
Nothing already there is replaced: a photo keeps its caption, tags or read text in the
profile, and only what is missing is filled in (method ``imported``).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import selections
from .geotag import RIFFLE_NS
from .paths import read_text

NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "riffle": RIFFLE_NS,
    "lr": "http://ns.adobe.com/lightroom/1.0/",
}
EMBEDDED = {".jpg", ".jpeg", ".png"}
# Descriptions cameras write on their own: not captions.
CAMERA_TEXT = re.compile(
    r"^(OLYMPUS DIGITAL CAMERA|SONY DSC|DCIM.*|Digital Camera|KODAK Digital Still Camera|"
    r"SAMSUNG|MINOLTA DIGITAL CAMERA|default|Default|Image|Picture|Photo)$"
)
METHOD = "imported"


@dataclass
class Found:
    caption: str = ""
    tags: list[str] = field(default_factory=list)
    text: str = ""
    translation: str = ""

    def empty(self) -> bool:
        return not (self.caption or self.tags or self.text)

    def fill(self, other: Found) -> None:
        """Take the parts this one does not have yet from ``other``."""
        if not self.caption:
            self.caption = other.caption
        if not self.tags:
            self.tags = other.tags
        if not self.text:
            self.text, self.translation = other.text, other.translation


# ---- the formats -------------------------------------------------------------------------


def parse_xmp(data: bytes | str) -> Found:
    """Caption, tags and read text from an XMP packet (empty when it has none)."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    start, end = data.find("<x:xmpmeta"), data.rfind("</x:xmpmeta>")
    if start < 0:
        start, end = data.find("<rdf:RDF"), data.rfind("</rdf:RDF>")
        end = end + len("</rdf:RDF>") if end >= 0 else -1
    else:
        end = end + len("</x:xmpmeta>") if end >= 0 else -1
    if start < 0 or end < 0:
        return Found()
    try:
        root = ET.fromstring(data[start:end])
    except ET.ParseError:
        return Found()
    out = Found()
    # darktable's automatic tags ("darktable|format|jpg") are also written flattened into
    # dc:subject; their parts are left out unless another keyword has them too.
    auto, own = set(), set()
    for li in root.iterfind(".//lr:hierarchicalSubject/rdf:Bag/rdf:li", NS):
        parts = [p.strip() for p in (li.text or "").split("|") if p.strip()]
        (auto if parts[:1] == ["darktable"] else own).update(parts)
    auto -= own
    for desc in root.iter(f"{{{NS['rdf']}}}Description"):
        if not out.caption:
            items = desc.findall("dc:description/rdf:Alt/rdf:li", NS)
            default = [i for i in items if i.get("{http://www.w3.org/XML/1998/namespace}lang") == "x-default"]
            li = (default or items or [None])[0]
            if li is not None and (li.text or "").strip():
                out.caption = li.text.strip()
        if not out.tags:
            out.tags = [
                li.text.strip() for li in desc.findall("dc:subject/rdf:Bag/rdf:li", NS)
                if (li.text or "").strip() and li.text.strip() not in auto
            ]
        for name in ("text", "translation"):
            value = desc.findtext(f"riffle:{name}", default=None, namespaces=NS) or desc.get(f"{{{RIFFLE_NS}}}{name}")
            if value and not getattr(out, name):
                setattr(out, name, value.strip())
    if out.caption and out.text:
        # Export appends the read text to the description ("\n\n" and its lines): take it off.
        block = "\n".join(p for p in (out.text, out.translation) if p)
        if out.caption == block:
            out.caption = ""
        elif out.caption.endswith("\n\n" + block):
            out.caption = out.caption[: -len(block) - 2].rstrip()
    if CAMERA_TEXT.match(out.caption):
        out.caption = ""
    return out


def embedded_xmp(path: Path) -> bytes | None:
    """The XMP packet inside a JPEG or PNG (read from the file's header only)."""
    try:
        with Image.open(path) as im:
            xmp = im.info.get("xmp") or im.info.get("XML:com.adobe.xmp")
    except Exception:  # noqa: BLE001 - unreadable: nothing to import
        return None
    if isinstance(xmp, str):
        xmp = xmp.encode()
    return xmp or None


def _looks_like_tags(line: str) -> bool:
    """A comma-separated list of short items (not a sentence)."""
    if line.endswith((".", "!", "?")):
        return False
    items = [i.strip() for i in line.split(",")]
    if any(not i for i in items):
        return False
    return all(len(i.split()) <= 4 for i in items) and (len(items) > 1 or len(items[0].split()) <= 3)


def parse_txt(text: str) -> Found:
    """Tags, a caption, or a caption and then tags (what export writes); what follows is
    read text without a marker, and left out."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return Found()
    if _looks_like_tags(lines[0]):
        return Found(tags=[t.strip() for t in lines[0].split(",")])
    found = Found(caption=lines[0])
    if len(lines) > 1 and _looks_like_tags(lines[1]):
        found.tags = [t.strip() for t in lines[1].split(",")]
    return found


def parse_jsonl_row(row: dict) -> Found:
    tags = [str(t).strip() for t in row.get("tags") or [] if str(t).strip()]
    caption = str(row.get("text") or "").strip()
    if caption and tags and caption == ", ".join(tags):
        caption = ""  # the tags stood in for a caption
    return Found(caption=caption, tags=tags, text=str(row.get("ocr_text") or "").strip(),
                 translation=str(row.get("ocr_translation") or "").strip())


# ---- a folder ------------------------------------------------------------------------------


def _jsonl_rows(folder: Path, files: list[Path]) -> dict[Path, Found]:
    """Rows of every metadata.jsonl in ``folder`` beside or above the photos, by the
    image each names (its ``file_name`` is relative to the JSONL's folder)."""
    dirs: set[Path] = set()
    for f in files:
        d = f.parent
        while d not in dirs and (d == folder or d.is_relative_to(folder)):
            dirs.add(d)
            d = d.parent
    out: dict[Path, Found] = {}
    for d in sorted(dirs):
        meta = d / "metadata.jsonl"
        if not meta.is_file():
            continue
        for line in read_text(meta).splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("file_name"):
                out.setdefault((d / row["file_name"]).resolve(), parse_jsonl_row(row))
    return out


def read_photo(path: Path, jsonl: dict[Path, Found] | None = None) -> Found:
    """Everything the export formats hold for the image at ``path``."""
    found = Found()
    if jsonl:
        row = jsonl.get(path.resolve())
        if row is not None:
            found.fill(row)
    for side in (path.with_suffix(".xmp"), path.with_name(path.name + ".xmp")):
        if side.is_file():
            try:
                found.fill(parse_xmp(side.read_bytes()))
            except OSError:
                pass
            break
    if path.suffix.lower() in EMBEDDED and not (found.caption and found.tags and found.text):
        xmp = embedded_xmp(path)
        if xmp:
            found.fill(parse_xmp(xmp))
    txt = path.with_suffix(".txt")
    if (not found.caption or not found.tags) and txt.is_file():
        found.fill(parse_txt(read_text(txt)))
    return found


def import_folder(catalogue: sqlite3.Connection, sel_path: str | Path, folder: Path, progress=None) -> dict[str, int]:
    """Fill in the captions, tags and read text of the photos in ``folder`` from the
    export formats, in the profile ``sel_path``. Returns counts of what was imported."""
    folder = Path(folder)
    rows = catalogue.execute(
        "SELECT id, sha256, source, rel_path FROM photos WHERE status = 'ok' ORDER BY id"
    ).fetchall()
    rows = [r for r in rows if (Path(r["source"]) / r["rel_path"]).is_relative_to(folder)]
    counts = {"photos": len(rows), "captions": 0, "tags": 0, "texts": 0}
    if not rows:
        return counts
    paths = {r["id"]: Path(r["source"]) / r["rel_path"] for r in rows}
    jsonl = _jsonl_rows(folder, list(paths.values()))

    conn = selections.connect(sel_path)
    try:
        state = selections._state(conn, sorted({r["sha256"] for r in rows}))
        now = time.time()
        it = progress(rows, total=len(rows), desc="importing") if progress else rows
        with conn:
            for r in it:
                have = state[r["sha256"]]
                if have["caption"] and have["tags"] and have["ocr"] is not None:
                    continue
                found = read_photo(paths[r["id"]], jsonl)
                if found.empty():
                    continue
                caption = found.caption if found.caption and not have["caption"] else ...
                tags = [(t, METHOD) for t in selections.clean_tags(found.tags)] if found.tags and not have["tags"] else ...
                if caption is not ... or tags is not ...:
                    selections._write(conn, r, caption, tags, method=METHOD, edited=True, now=now)
                    counts["captions"] += caption is not ...
                    counts["tags"] += tags is not ...
                if found.text and have["ocr"] is None:
                    selections._write_text(conn, r, {"text": found.text, "translation": found.translation},
                                           method=METHOD, edited=True, now=now)
                    counts["texts"] += 1
                    have["ocr"] = {}  # (a photo listed twice: once is enough)
                if caption is not ...:
                    have["caption"] = caption
                if tags is not ...:
                    have["tags"] = tags
    finally:
        conn.close()
    return counts
