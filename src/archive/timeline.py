"""Phone location history: parse exports into positions over time.

Supported (detected from the content, not the file name):

- Google Maps Timeline, exported from the phone (2024+): ``semanticSegments``
  with visits, activities and path points (Android: ``"52.5°, 13.4°"``
  strings; iOS: a top-level list with ``"geo:52.5,13.4"`` strings), plus
  ``rawSignals`` positions.
- Google Takeout ``Records.json`` (older exports): ``locations`` with E7 coordinates.
- GPX tracks (``trkpt``/``wpt`` with ``time``).

The files are only read; nothing leaves the machine.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_LATLNG = re.compile(r"(-?\d+(?:\.\d+)?)°?\s*,\s*(-?\d+(?:\.\d+)?)°?")


def parse_latlng(value) -> tuple[float, float] | None:
    """``"52.5°, 13.4°"``, ``"geo:52.5,13.4"`` or ``{"latLng": ...}`` -> (lat, lon)."""
    if isinstance(value, dict):
        value = value.get("latLng") or value.get("LatLng") or value.get("placeLocation")
        if isinstance(value, dict):
            value = value.get("latLng")
    if not isinstance(value, str):
        return None
    m = _LATLNG.search(value.removeprefix("geo:"))
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def parse_time(value) -> float | None:
    """ISO 8601 (with offset or Z) -> POSIX seconds."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # a local time without offset cannot be placed on the timeline
    return dt.timestamp()


def _offset_minutes(value: str) -> int | None:
    try:
        off = datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset()
    except (ValueError, AttributeError):
        return None
    return None if off is None else int(off.total_seconds() // 60)


@dataclass
class History:
    """Merged positions; each array is sorted by time."""

    visits: list[tuple] = field(default_factory=list)  # (start, end, lat, lon, prob)
    routes: list[tuple] = field(default_factory=list)  # (start, end, lat0, lon0, lat1, lon1)
    points: list[tuple] = field(default_factory=list)  # (t, lat, lon, accuracy_m)
    tz: list[tuple] = field(default_factory=list)  # (start, end, utc offset minutes)
    formats: list[str] = field(default_factory=list)

    def extend(self, other: History) -> None:
        self.visits += other.visits
        self.routes += other.routes
        self.points += other.points
        self.tz += other.tz
        self.formats += other.formats

    def finish(self) -> History:
        for name in ("visits", "routes", "points", "tz"):
            getattr(self, name).sort()
        return self

    def span(self) -> tuple[float, float] | None:
        starts = [x[0] for x in self.visits + self.routes + self.points]
        ends = [x[1] for x in self.visits + self.routes] + [p[0] for p in self.points]
        return (min(starts), max(ends)) if starts else None

    def summary(self) -> dict:
        span = self.span()
        day = lambda t: datetime.fromtimestamp(t).date().isoformat()
        return {
            "format": ", ".join(dict.fromkeys(self.formats)) or "unknown",
            "from": day(span[0]) if span else None,
            "to": day(span[1]) if span else None,
            "visits": len(self.visits),
            "routes": len(self.routes),
            "points": len(self.points),
        }


def _parse_segments(segments: list, h: History) -> None:
    for s in segments:
        if not isinstance(s, dict):
            continue
        start, end = parse_time(s.get("startTime")), parse_time(s.get("endTime"))
        if start is None or end is None:
            continue
        if isinstance(s.get("startTime"), str):
            off = s.get("startTimeTimezoneUtcOffsetMinutes")
            if off is None:
                off = _offset_minutes(s["startTime"])
            if off is not None:
                h.tz.append((start, end, int(off)))
        if "visit" in s:
            v = s["visit"]
            cand = v.get("topCandidate") or {}
            pos = parse_latlng(cand.get("placeLocation"))
            if pos:
                prob = float(v.get("probability") or cand.get("probability") or 0)
                h.visits.append((start, end, pos[0], pos[1], prob))
        elif "activity" in s:
            a = s["activity"]
            p0, p1 = parse_latlng(a.get("start")), parse_latlng(a.get("end"))
            if p0 and p1:
                h.routes.append((start, end, p0[0], p0[1], p1[0], p1[1]))
        elif "timelinePath" in s:
            for p in s["timelinePath"] or []:
                pos = parse_latlng(p.get("point"))
                t = parse_time(p.get("time"))
                if t is None and "durationMinutesOffsetFromStartTime" in p:  # iOS
                    try:
                        t = start + float(p["durationMinutesOffsetFromStartTime"]) * 60
                    except (TypeError, ValueError):
                        t = None
                if pos and t is not None:
                    h.points.append((t, pos[0], pos[1], 50.0))


def _parse_raw_signals(signals: list, h: History) -> None:
    for r in signals:
        p = r.get("position") if isinstance(r, dict) else None
        if not p:
            continue
        pos, t = parse_latlng(p.get("LatLng") or p.get("latLng")), parse_time(p.get("timestamp"))
        if pos and t is not None:
            h.points.append((t, pos[0], pos[1], float(p.get("accuracyMeters") or 50)))


def _parse_records(locations: list, h: History) -> None:
    for r in locations:
        try:
            lat, lon = r["latitudeE7"] / 1e7, r["longitudeE7"] / 1e7
        except (KeyError, TypeError):
            continue
        t = parse_time(r.get("timestamp"))
        if t is None and "timestampMs" in r:
            t = int(r["timestampMs"]) / 1000
        if t is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            h.points.append((t, lat, lon, float(r.get("accuracy") or 50)))


def _parse_gpx(path: Path, h: History) -> None:
    for _, el in ET.iterparse(path):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("trkpt", "wpt", "rtept"):
            time_el = next((c for c in el if c.tag.rsplit("}", 1)[-1] == "time"), None)
            t = parse_time(time_el.text.strip()) if time_el is not None and time_el.text else None
            try:
                lat, lon = float(el.get("lat")), float(el.get("lon"))
            except (TypeError, ValueError):
                lat = lon = None
            if t is not None and lat is not None:
                h.points.append((t, lat, lon, 20.0))
            el.clear()


class HistoryError(ValueError):
    pass


def load_file(path: str | Path) -> History:
    path = Path(path)
    h = History()
    if path.suffix.lower() == ".gpx":
        try:
            _parse_gpx(path, h)
        except ET.ParseError as e:
            raise HistoryError(f"{path.name}: not a valid GPX file ({e})")
        h.formats.append("GPX")
        return h.finish()
    try:
        with open(path, "rb") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise HistoryError(f"{path.name}: not a readable JSON file ({e})")
    if isinstance(data, dict) and "semanticSegments" in data:
        _parse_segments(data["semanticSegments"], h)
        _parse_raw_signals(data.get("rawSignals") or [], h)
        h.formats.append("Google Timeline (phone export)")
    elif isinstance(data, list) and data and isinstance(data[0], dict) and "startTime" in data[0]:
        _parse_segments(data, h)
        h.formats.append("Google Timeline (iOS export)")
    elif isinstance(data, dict) and "locations" in data:
        _parse_records(data["locations"], h)
        h.formats.append("Google Takeout Records.json")
    else:
        raise HistoryError(f"{path.name}: not a recognised location history (Google Timeline, Records.json, or GPX)")
    if not (h.visits or h.routes or h.points):
        raise HistoryError(f"{path.name}: no positions found")
    return h.finish()


_cache: dict[tuple, History] = {}


def load(paths: list[Path]) -> History:
    """Merge several files; parsed files are cached by (path, size, mtime)."""
    merged = History()
    for p in paths:
        st = Path(p).stat()
        key = (str(p), st.st_size, st.st_mtime_ns)
        if key not in _cache:
            if len(_cache) > 8:
                _cache.clear()
            _cache[key] = load_file(p)
        merged.extend(_cache[key])
    return merged.finish()


def signature(paths: list[Path]) -> str:
    parts = []
    for p in paths:
        try:
            st = Path(p).stat()
            parts.append(f"{p}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{p}:missing")
    return "|".join(parts)
