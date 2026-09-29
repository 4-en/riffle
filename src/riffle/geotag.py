"""Add a GPS position, a caption, and keywords to exported copies (never to originals).

Only the metadata at the start of the file changes; the image data is copied as
is, so there is no re-encoding. A tagged copy is described as a new ``head``
(bytes) followed by the source file from ``rest`` onwards.

- JPEG and PNG: a GPS IFD is added to the EXIF data *without moving any existing
  byte*: a copy of IFD0 with a GPSInfo pointer and the new GPS IFD are appended
  at the end, and the TIFF header is pointed at the new IFD0. Every existing
  offset stays valid, so MakerNotes (even those with absolute offsets, e.g. Canon)
  and the embedded thumbnail (IFD1) survive untouched. (Rebuilding EXIF with
  Pillow would drop the thumbnail.) If the result does not fit a JPEG segment,
  the position goes into an XMP block instead.
- A caption and keywords (captions and fixed tags, selections.py) go into an XMP
  block (``dc:description``, ``dc:subject``): a JPEG APP1 segment or a PNG iTXt
  chunk, with the position too when it could not go into EXIF. A file that
  already has XMP gets a sidecar instead (merging XMP is not attempted).
- Anything else (RAW, TIFF, HEIC), or when the above is not possible: an XMP
  sidecar next to the copy (``<name>.xmp``), which Lightroom, Capture One and
  darktable read.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

GPS_IFD = 0x8825
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
HEAD_LIMIT = 8 << 20  # metadata beyond this is not expected; fall back to a sidecar


@dataclass
class Tagged:
    head: bytes  # new start of the file
    rest: int  # the source file continues from this offset
    method: str  # "exif" or "xmp"


# TIFF field types
_BYTE, _ASCII, _LONG, _RATIONAL = 1, 2, 4, 5
_TYPE_SIZE = {_BYTE: 1, _ASCII: 1, _LONG: 4, _RATIONAL: 8}


def _rational_dms(value: float) -> list[tuple[int, int]]:
    value = abs(value)
    deg = int(value)
    minutes = int((value - deg) * 60)
    seconds = (value - deg - minutes / 60) * 3600
    return [(deg, 1), (minutes, 1), (round(seconds * 10000), 10000)]


def gps_entries(lat: float, lon: float, accuracy_m: float | None) -> list[tuple[int, int, object]]:
    """(tag, type, value) for the GPS IFD (EXIF 2.3)."""
    entries = [
        (0, _BYTE, bytes([2, 3, 0, 0])),  # GPSVersionID
        (1, _ASCII, "N" if lat >= 0 else "S"),
        (2, _RATIONAL, _rational_dms(lat)),
        (3, _ASCII, "E" if lon >= 0 else "W"),
        (4, _RATIONAL, _rational_dms(lon)),
        (18, _ASCII, "WGS-84"),  # GPSMapDatum
    ]
    if accuracy_m:
        entries.append((31, _RATIONAL, [(round(accuracy_m * 10), 10)]))  # GPSHPositioningError
    return entries


def _encode(bo: str, ftype: int, value) -> tuple[int, bytes]:
    """(count, raw value bytes) for a TIFF field."""
    if ftype == _ASCII:
        raw = value.encode("ascii") + b"\x00"
        return len(raw), raw
    if ftype == _BYTE:
        return len(value), bytes(value)
    if ftype == _RATIONAL:
        return len(value), b"".join(struct.pack(bo + "II", n, d) for n, d in value)
    raise ValueError(ftype)


def add_gps_to_tiff(tiff: bytes | None, lat: float, lon: float, accuracy_m: float | None) -> bytes:
    """EXIF data (a TIFF structure) with a GPS IFD added, appending only.

    The original bytes are kept in place; a new IFD0 (the old entries plus a
    GPSInfo pointer, same link to IFD1) and the GPS IFD go at the end, and the
    header points at the new IFD0. With no EXIF yet, a minimal one is created."""
    if not tiff:
        tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 0) + struct.pack("<I", 0)
    if tiff[:4] not in (b"II*\x00", b"MM\x00*"):
        raise ValueError("not EXIF/TIFF data")
    bo = "<" if tiff[:2] == b"II" else ">"
    ifd0 = struct.unpack(bo + "I", tiff[4:8])[0]
    count = struct.unpack(bo + "H", tiff[ifd0 : ifd0 + 2])[0]
    entries = [tiff[ifd0 + 2 + 12 * i : ifd0 + 14 + 12 * i] for i in range(count)]
    next_ifd = tiff[ifd0 + 2 + 12 * count : ifd0 + 6 + 12 * count]  # link to IFD1 (thumbnail)
    if len(next_ifd) != 4 or any(len(e) != 12 for e in entries):
        raise ValueError("truncated IFD0")
    entries = [e for e in entries if struct.unpack(bo + "H", e[:2])[0] != GPS_IFD]

    out = bytearray(tiff)
    if len(out) % 2:
        out += b"\x00"
    new_ifd0 = len(out)
    gps_at = new_ifd0 + 2 + 12 * (len(entries) + 1) + 4
    entries.append(struct.pack(bo + "HHII", GPS_IFD, _LONG, 1, gps_at))
    entries.sort(key=lambda e: struct.unpack(bo + "H", e[:2])[0])  # TIFF wants ascending tags
    out += struct.pack(bo + "H", len(entries)) + b"".join(entries) + next_ifd

    fields = gps_entries(lat, lon, accuracy_m)
    values_at = gps_at + 2 + 12 * len(fields) + 4
    ifd, values = bytearray(struct.pack(bo + "H", len(fields))), bytearray()
    for tag_id, ftype, value in fields:
        n, raw = _encode(bo, ftype, value)
        if len(raw) <= 4:
            ifd += struct.pack(bo + "HHI", tag_id, ftype, n) + raw.ljust(4, b"\x00")
        else:
            ifd += struct.pack(bo + "HHII", tag_id, ftype, n, values_at + len(values))
            values += raw + (b"\x00" if len(raw) % 2 else b"")
    ifd += struct.pack(bo + "I", 0)
    out += ifd + values
    out[4:8] = struct.pack(bo + "I", new_ifd0)
    return bytes(out)


def _exif_with_gps(tiff: bytes | None, lat, lon, accuracy_m) -> bytes | None:
    try:
        return add_gps_to_tiff(tiff, lat, lon, accuracy_m)
    except (ValueError, struct.error):
        return None


def _xmp_coord(value: float, pos: str, neg: str) -> str:
    deg = int(abs(value))
    minutes = (abs(value) - deg) * 60
    return f"{deg},{minutes:.6f}{pos if value >= 0 else neg}"


@dataclass
class Meta:
    """What goes into a copy: a position and/or a caption and keywords."""

    lat: float | None = None
    lon: float | None = None
    accuracy_m: float | None = None
    description: str = ""
    keywords: tuple[str, ...] = ()
    text: str = ""  # text read from the photo and its translation, in Riffle's own fields
    translation: str = ""

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def has_text(self) -> bool:
        return bool(self.description or self.keywords)


RIFFLE_NS = "urn:riffle:xmp:1.0"  # Riffle's own XMP fields (sidecars.py reads them back)


def xmp_packet(
    lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None, description: str = "", keywords=(),
    text: str = "", translation: str = "",
) -> bytes:
    """An XMP packet with a GPS position and/or dc:description and dc:subject; ``text``
    and ``translation`` (read from the photo) go into riffle:text / riffle:translation,
    so an import can tell them from the caption they also follow in the description."""
    attrs, body = "", ""
    if lat is not None and lon is not None:
        acc = f' exif:GPSHPositioningError="{round(accuracy_m * 10)}/10"' if accuracy_m else ""
        attrs = (
            '\n   exif:GPSVersionID="2.3.0.0"\n'
            f'   exif:GPSLatitude="{_xmp_coord(lat, "N", "S")}"\n'
            f'   exif:GPSLongitude="{_xmp_coord(lon, "E", "W")}"\n'
            f'   exif:GPSMapDatum="WGS-84"{acc}'
        )
    if description:
        body += f'   <dc:description><rdf:Alt><rdf:li xml:lang="x-default">{escape(description)}</rdf:li></rdf:Alt></dc:description>\n'
    if keywords:
        items = "".join(f"<rdf:li>{escape(k)}</rdf:li>" for k in keywords)
        body += f"   <dc:subject><rdf:Bag>{items}</rdf:Bag></dc:subject>\n"
    if text:
        body += f"   <riffle:text>{escape(text)}</riffle:text>\n"
    if translation:
        body += f"   <riffle:translation>{escape(translation)}</riffle:translation>\n"
    close = f">\n{body}  </rdf:Description>\n" if body else "/>\n"
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about="" xmlns:exif="http://ns.adobe.com/exif/1.0/"'
        f' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        f'{f" xmlns:riffle=\"{RIFFLE_NS}\"" if text or translation else ""}{attrs}{close}'
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    ).encode()


def _apply(data: bytes, edits: list[tuple[int, int, bytes]]) -> tuple[bytes, int]:
    """The new head and where the source continues: ``edits`` replace data[start:end]
    (an insertion has start == end); in order, stable for equal starts."""
    end = max(e for _, e, _ in edits)
    out, at = bytearray(), 0
    for start, stop, new in sorted(edits, key=lambda e: e[0]):
        out += data[at:start] + new
        at = max(at, stop)
    out += data[at:end]
    return bytes(out), end


def _read_head(path: Path, stop) -> bytes | None:
    """Read the start of a file in growing chunks until ``stop(data)`` is true."""
    size = 1 << 16
    with open(path, "rb") as f:
        data = f.read(size)
        while not stop(data):
            if len(data) >= HEAD_LIMIT:
                return None
            more = f.read(size)
            if not more:
                return data if stop(data) else None
            data += more
            size *= 2
    return data


def _tag_jpeg(path: Path, meta: Meta) -> Tagged | None:
    def segments(data):
        """(marker, start, end) of the segments before the image data, or None if incomplete."""
        if data[:2] != b"\xff\xd8":
            return []
        out, i = [], 2
        while i + 4 <= len(data):
            if data[i] != 0xFF:
                return []
            marker = data[i + 1]
            if marker == 0xDA:  # start of scan: image data follows
                return out
            length = struct.unpack(">H", data[i + 2 : i + 4])[0]
            if i + 2 + length > len(data):
                return None
            out.append((marker, i, i + 2 + length))
            i += 2 + length
        return None

    def app1(payload: bytes) -> bytes:
        return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload

    data = _read_head(path, lambda d: segments(d) is not None)
    segs = segments(data) if data else None
    if not segs:
        return None
    exif_seg = next((s for s in segs if s[0] == 0xE1 and data[s[1] + 4 : s[1] + 10] == b"Exif\x00\x00"), None)
    has_xmp = any(s[0] == 0xE1 and data[s[1] + 4 : s[1] + 4 + len(XMP_HEADER)] == XMP_HEADER for s in segs)
    # New segments go after the JFIF APP0 if there is one, else right after SOI.
    first = segs[0][2] if segs[0][0] == 0xE0 else 2
    edits, method, gps_in_xmp = [], "xmp", False
    if meta.has_gps:
        tiff = data[exif_seg[1] + 10 : exif_seg[2]] if exif_seg else None
        new_tiff = _exif_with_gps(tiff, meta.lat, meta.lon, meta.accuracy_m)
        if new_tiff is not None and len(new_tiff) + 8 <= 0xFFFF:
            segment = app1(b"Exif\x00\x00" + new_tiff)
            edits.append((exif_seg[1], exif_seg[2], segment) if exif_seg else (first, first, segment))
            method = "exif"
        else:
            gps_in_xmp = True  # EXIF must stay untouched: the position goes into XMP
    if meta.has_text or gps_in_xmp:
        if has_xmp:
            return None  # would have to merge with the existing XMP: sidecar instead
        packet = xmp_packet(
            meta.lat if gps_in_xmp else None, meta.lon if gps_in_xmp else None, meta.accuracy_m, meta.description, meta.keywords,
            meta.text, meta.translation,
        )
        at = exif_seg[2] if exif_seg else first
        edits.append((at, at, app1(XMP_HEADER + packet)))
        if gps_in_xmp:
            method = "xmp"
    if not edits:
        return None
    head, rest = _apply(data, edits)
    return Tagged(head, rest, method)


def _tag_png(path: Path, meta: Meta) -> Tagged | None:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunks(data):
        """(type, start, end) of the chunks before the first IDAT, or None if incomplete."""
        if data[:8] != sig:
            return []
        out, i = [], 8
        while i + 8 <= len(data):
            length, ctype = struct.unpack(">I4s", data[i : i + 8])
            if ctype == b"IDAT":
                return out
            end = i + 12 + length
            if end > len(data):
                return None
            out.append((ctype, i, end))
            i = end
        return None

    def chunk(ctype: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)

    data = _read_head(path, lambda d: chunks(d) is not None)
    found = chunks(data) if data else None
    if not found:
        return None
    at = found[-1][2]  # after the last header chunk, before the image data
    edits, method = [], "xmp"
    if meta.has_gps:
        exif_chunk = next((c for c in found if c[0] == b"eXIf"), None)
        tiff = data[exif_chunk[1] + 8 : exif_chunk[2] - 4] if exif_chunk else None
        new_tiff = _exif_with_gps(tiff, meta.lat, meta.lon, meta.accuracy_m)
        if new_tiff is None:
            return None
        new = chunk(b"eXIf", new_tiff)
        edits.append((exif_chunk[1], exif_chunk[2], new) if exif_chunk else (at, at, new))
        method = "exif"
    if meta.has_text:
        xmp_key = b"XML:com.adobe.xmp\x00"
        if any(c[0] == b"iTXt" and data[c[1] + 8 : c[1] + 8 + len(xmp_key)] == xmp_key for c in found):
            return None  # already has XMP: sidecar instead
        packet = xmp_packet(description=meta.description, keywords=meta.keywords, text=meta.text, translation=meta.translation)
        edits.append((at, at, chunk(b"iTXt", xmp_key + b"\x00\x00\x00\x00" + packet)))
    if not edits:
        return None
    head, rest = _apply(data, edits)
    return Tagged(head, rest, method)


def tag(path: Path, lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None, *, meta: Meta | None = None) -> Tagged | None:
    """How to write a tagged copy of ``path`` (a position, or ``meta`` with a caption
    and keywords too), or None if it needs a sidecar."""
    meta = meta or Meta(lat, lon, accuracy_m)
    suffix = path.suffix.lower()
    try:
        if suffix in (".jpg", ".jpeg"):
            return _tag_jpeg(path, meta)
        if suffix == ".png":
            return _tag_png(path, meta)
    except (OSError, ValueError, struct.error, SyntaxError):
        return None
    return None
