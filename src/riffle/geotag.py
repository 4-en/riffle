"""Add a GPS position to exported copies (never to originals).

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
- Anything else (RAW, TIFF, HEIC), or when the above is not possible: an XMP
  sidecar next to the copy (``<name>.xmp``), which Lightroom, Capture One and
  darktable read.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

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


def xmp_packet(lat: float, lon: float, accuracy_m: float | None) -> bytes:
    acc = f' exif:GPSHPositioningError="{round(accuracy_m * 10)}/10"' if accuracy_m else ""
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about="" xmlns:exif="http://ns.adobe.com/exif/1.0/"\n'
        '   exif:GPSVersionID="2.3.0.0"\n'
        f'   exif:GPSLatitude="{_xmp_coord(lat, "N", "S")}"\n'
        f'   exif:GPSLongitude="{_xmp_coord(lon, "E", "W")}"\n'
        f'   exif:GPSMapDatum="WGS-84"{acc}/>\n'
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    ).encode()


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


def _tag_jpeg(path: Path, lat, lon, accuracy_m) -> Tagged | None:
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

    data = _read_head(path, lambda d: segments(d) is not None)
    segs = segments(data) if data else None
    if not segs:
        return None
    exif_seg = next((s for s in segs if s[0] == 0xE1 and data[s[1] + 4 : s[1] + 10] == b"Exif\x00\x00"), None)
    tiff = data[exif_seg[1] + 10 : exif_seg[2]] if exif_seg else None
    new_tiff = _exif_with_gps(tiff, lat, lon, accuracy_m)
    if new_tiff is not None and len(new_tiff) + 8 <= 0xFFFF:
        payload = b"Exif\x00\x00" + new_tiff
        segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
        if exif_seg:
            head = data[: exif_seg[1]] + segment
            return Tagged(head, exif_seg[2], "exif")
        # No EXIF yet: after the JFIF APP0 if there is one, else right after SOI.
        at = segs[0][2] if segs[0][0] == 0xE0 else 2
        return Tagged(data[:at] + segment, at, "exif")
    # EXIF must stay untouched: embed XMP instead, unless the file already has XMP.
    if any(s[0] == 0xE1 and data[s[1] + 4 : s[1] + 4 + len(XMP_HEADER)] == XMP_HEADER for s in segs):
        return None
    payload = XMP_HEADER + xmp_packet(lat, lon, accuracy_m)
    segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    at = exif_seg[2] if exif_seg else 2
    return Tagged(data[:at] + segment, at, "xmp")


def _tag_png(path: Path, lat, lon, accuracy_m) -> Tagged | None:
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

    data = _read_head(path, lambda d: chunks(d) is not None)
    found = chunks(data) if data else None
    if not found:
        return None
    exif_chunk = next((c for c in found if c[0] == b"eXIf"), None)
    tiff = data[exif_chunk[1] + 8 : exif_chunk[2] - 4] if exif_chunk else None
    new_tiff = _exif_with_gps(tiff, lat, lon, accuracy_m)
    if new_tiff is None:
        return None
    chunk = struct.pack(">I", len(new_tiff)) + b"eXIf" + new_tiff
    chunk += struct.pack(">I", zlib.crc32(b"eXIf" + new_tiff) & 0xFFFFFFFF)
    if exif_chunk:
        return Tagged(data[: exif_chunk[1]] + chunk, exif_chunk[2], "exif")
    at = found[-1][2]  # after the last header chunk, before the image data
    return Tagged(data[:at] + chunk, at, "exif")


def tag(path: Path, lat: float, lon: float, accuracy_m: float | None) -> Tagged | None:
    """How to write a tagged copy of ``path``, or None if it needs a sidecar."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".jpg", ".jpeg"):
            return _tag_jpeg(path, lat, lon, accuracy_m)
        if suffix == ".png":
            return _tag_png(path, lat, lon, accuracy_m)
    except (OSError, ValueError, struct.error, SyntaxError):
        return None
    return None
