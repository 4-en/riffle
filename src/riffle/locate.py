"""Where each photo was taken: camera GPS, or the phone's location history.

For a photo without GPS, its capture time (made absolute with the EXIF offset,
or the timeline's own offset for that local time) is looked up in the history:

1. inside a *visit* (the phone stayed at one place): that place;
2. inside an *activity* (moving between places): interpolated along the route,
   between the nearest recorded points around that time;
3. otherwise, between the nearest points if both are within ``max_gap_minutes``
   (``route``), or next to a single point within half of that (``nearby``).

Places are named offline with the bundled GeoNames dataset (reverse_geocode):
nearest town or village, region (state/province), and country.
"""

from __future__ import annotations

import bisect
import hashlib
import logging
import math
import re
import sqlite3
from dataclasses import dataclass

from .config import Config
from .stacks import exif_seconds
from .timeline import History, load

log = logging.getLogger(__name__)

TIMELINE_SOURCES = ("visit", "route", "nearby")
MAX_ROUTE_ACCURACY = 5000.0


@dataclass
class Fix:
    lat: float
    lon: float
    source: str
    accuracy_m: float
    gap_s: float = 0.0


def distance_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(a)))


_OFFSET = re.compile(r"^([+-])(\d{1,2}):?(\d{2})$")


def offset_seconds(tz: str | None) -> int | None:
    m = _OFFSET.match(tz.strip()) if tz else None
    if not m:
        return None
    sign = 1 if m.group(1) == "+" else -1
    return sign * (int(m.group(2)) * 3600 + int(m.group(3)) * 60)


class Locator:
    """Looks up positions in a History by absolute time."""

    def __init__(self, history: History, max_gap_s: float):
        self.h = history
        self.max_gap = max_gap_s
        self.visit_starts = [v[0] for v in history.visits]
        self.max_visit = max((v[1] - v[0] for v in history.visits), default=0)
        self.route_starts = [r[0] for r in history.routes]
        self.max_route = max((r[1] - r[0] for r in history.routes), default=0)
        self.point_times = [p[0] for p in history.points]
        self.tz_starts = [z[0] for z in history.tz]
        self.max_tz = max((z[1] - z[0] for z in history.tz), default=0)

    def utc(self, taken_at: str | None, tz: str | None) -> float | None:
        """Absolute capture time; EXIF offset first, else the timeline's offset."""
        local = exif_seconds(taken_at)
        if local is None:
            return None
        off = offset_seconds(tz)
        if off is not None:
            return local - off
        # The local time lies in a timeline segment once shifted by that segment's offset.
        hi = bisect.bisect_right(self.tz_starts, local + 15 * 3600)
        for i in range(hi - 1, -1, -1):
            start, end, minutes = self.h.tz[i]
            if start < local - 15 * 3600 - self.max_tz:
                break
            t = local - minutes * 60
            if start <= t <= end:
                return t
        return None

    def _containing(self, items, starts, longest, t):
        """Items (start, end, ...) containing t, found by scanning back from t."""
        i = bisect.bisect_right(starts, t)
        out = []
        while i > 0:
            i -= 1
            item = items[i]
            if item[0] < t - longest:
                break
            if item[0] <= t <= item[1]:
                out.append(item)
        return out

    def _points_between(self, lo: float, hi: float, t: float):
        """Nearest recorded points before and after t, within [lo, hi]."""
        i = bisect.bisect_left(self.point_times, t)
        before = self.h.points[i - 1] if i > 0 and self.point_times[i - 1] >= lo else None
        after = self.h.points[i] if i < len(self.point_times) and self.point_times[i] <= hi else None
        return before, after

    @staticmethod
    def _interpolate(a: tuple, b: tuple, t: float) -> tuple[float, float]:
        """a, b: (time, lat, lon)."""
        if b[0] <= a[0]:
            return a[1], a[2]
        f = min(1.0, max(0.0, (t - a[0]) / (b[0] - a[0])))
        return a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2])

    def locate(self, t: float) -> Fix | None:
        visits = self._containing(self.h.visits, self.visit_starts, self.max_visit, t)
        if visits:
            start, end, lat, lon, _ = min(visits, key=lambda v: v[1] - v[0])  # the most specific
            return Fix(lat, lon, "visit", 50.0)

        routes = self._containing(self.h.routes, self.route_starts, self.max_route, t)
        if routes:
            start, end, lat0, lon0, lat1, lon1 = min(routes, key=lambda r: r[1] - r[0])
            before, after = self._points_between(start, end, t)
            a = (before[0], before[1], before[2]) if before else (start, lat0, lon0)
            b = (after[0], after[1], after[2]) if after else (end, lat1, lon1)
            lat, lon = self._interpolate(a, b, t)
            spread = distance_m(a[1], a[2], b[1], b[2]) / 2
            return Fix(lat, lon, "route", min(MAX_ROUTE_ACCURACY, max(50.0, spread)), max(t - a[0], b[0] - t))

        before, after = self._points_between(t - self.max_gap, t + self.max_gap, t)
        if before and after:
            lat, lon = self._interpolate(before[:3], after[:3], t)
            spread = distance_m(before[1], before[2], after[1], after[2]) / 2
            acc = max(spread, before[3], after[3])
            return Fix(lat, lon, "route", min(MAX_ROUTE_ACCURACY, acc), max(t - before[0], after[0] - t))
        for p in (before, after):
            if p and abs(p[0] - t) <= self.max_gap / 2:
                return Fix(p[1], p[2], "nearby", max(p[3], 100.0), abs(p[0] - t))
        return None


def _signature(conn: sqlite3.Connection, cfg: Config) -> str:
    from .timeline import signature

    h = hashlib.sha1()
    h.update(signature(cfg.location_history).encode())
    h.update(f"{cfg.location.max_gap_minutes}|{cfg.location.min_population}|v1".encode())
    for row in conn.execute(
        "SELECT id, taken_at, tz_offset, lat, lon FROM photos WHERE status = 'ok' ORDER BY id"
    ):
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def locate_photos(conn: sqlite3.Connection, cfg: Config, force: bool = False) -> dict | None:
    """Recompute photo_locations. Skipped (returns None) when nothing it depends on
    changed. Returns the number of photos placed per source."""
    sig = _signature(conn, cfg)
    row = conn.execute("SELECT value FROM meta WHERE key = 'locate'").fetchone()
    if not force and row and row[0] == sig:
        return None

    history = load([p for p in cfg.location_history if p.exists()]) if cfg.location_history else History()
    for p in cfg.location_history:
        if not p.exists():
            log.warning("location history file not found: %s", p)
    locator = Locator(history, cfg.location.max_gap_minutes * 60)

    fixes: dict[int, Fix] = {}
    for r in conn.execute("SELECT id, taken_at, tz_offset, lat, lon FROM photos WHERE status = 'ok'"):
        if r["lat"] is not None and r["lon"] is not None:
            fixes[r["id"]] = Fix(r["lat"], r["lon"], "exif", 10.0)
            continue
        if not history.visits and not history.routes and not history.points:
            continue
        t = locator.utc(r["taken_at"], r["tz_offset"])
        if t is not None and (fix := locator.locate(t)) is not None:
            fixes[r["id"]] = fix

    names = _place_names([(f.lat, f.lon) for f in fixes.values()], cfg.location.min_population)
    conn.execute("DELETE FROM photo_locations")
    conn.executemany(
        """INSERT INTO photo_locations (photo_id, lat, lon, source, accuracy_m, gap_s,
               country_code, country, region, place)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (pid, f.lat, f.lon, f.source, f.accuracy_m, f.gap_s, *name)
            for (pid, f), name in zip(fixes.items(), names)
        ],
    )
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('locate', ?)", (sig,))
    conn.commit()
    counts: dict[str, int] = {}
    for f in fixes.values():
        counts[f.source] = counts.get(f.source, 0) + 1
    return counts


def _place_names(coords: list[tuple[float, float]], min_population: int) -> list[tuple[str, str, str, str]]:
    """(country_code, country, region, place) for each coordinate, offline."""
    if not coords:
        return []
    import reverse_geocode

    out = []
    for g in reverse_geocode.search(coords, min_population=min_population):
        out.append((
            g.get("country_code") or "",
            g.get("country") or g.get("country_code") or "",
            g.get("state") or "",
            g.get("city") or "",
        ))
    return out
