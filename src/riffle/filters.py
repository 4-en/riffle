"""Photo filters shared by listing, search, tag counts and facets.

Tags are AND-combined; values within one facet (cameras, lenses, orientations)
are OR-combined; different facets are AND-combined.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field

from fastapi import HTTPException, Query

from .selections import EXPORTED_EXPR, FLAG_EXPR

# EXIF dates are stored as written ("2024:05:01 10:00:00"); compare as "2024-05-01".
DATE_EXPR = "replace(substr(p.taken_at, 1, 10), ':', '-')"

# Group keys for the grouped listing: "2026-03-12", "2026-03", "2026"; NULL = undated.
# Location keys from photo_locations: "SE", "SE|Gotland", "SE|Gotland|Visby"; NULL = unknown.
_LOC = "(SELECT {expr} FROM photo_locations l WHERE l.photo_id = p.id)"
LOCATION_KEYS = {
    "country": _LOC.format(expr="l.country_code"),
    "region": _LOC.format(expr="l.country_code || '|' || l.region"),
    "place": _LOC.format(expr="l.country_code || '|' || l.region || '|' || l.place"),
}
LOCATION_SOURCE = f"COALESCE({_LOC.format(expr='l.source')}, 'none')"
LOCATION_SOURCES = ("exif", "visit", "route", "nearby", "none")

# Group keys for the grouped listing. Dates: "2026-03-12", "2026-03", "2026";
# locations: see LOCATION_KEYS. NULL = undated / unknown location.
# A photo's parent folder, e.g. "/photos/Trip/" + "day1/" (rtrim with every
# character except "/" strips the file name, leaving the folder with a trailing /).
DIR_EXPR = "rtrim(p.rel_path, replace(p.rel_path, '/', ''))"
FOLDER_KEY = f"p.source || '/' || {DIR_EXPR}"
FILENAME_EXPR = f"substr(p.rel_path, length({DIR_EXPR}) + 1)"
PLACE_NAME = _LOC.format(expr="l.place")  # for sorting by place name

GROUP_KEYS = {
    "day": DATE_EXPR,
    "month": f"substr({DATE_EXPR}, 1, 7)",
    "year": f"substr({DATE_EXPR}, 1, 4)",
    **LOCATION_KEYS,
    "folder": FOLDER_KEY,
}

# Sorts besides date that keep a grouping: they order the groups by name (location
# or folder label) and the photos within them by the same sort.
NAME_SORTS = {
    **{g: ("place", "-place") for g in LOCATION_KEYS},
    "folder": ("name", "-name"),
}

RANGE_COLUMNS = {
    "focal": "p.focal_length",
    "aperture": "p.aperture",
    "iso": "p.iso",
    "mp": "(p.width * p.height / 1e6)",  # resolution in megapixels
}

ORIENTATIONS = {
    "landscape": "p.width > p.height",
    "portrait": "p.height > p.width",
    "square": "p.width = p.height",
}

FLAG_VALUES = ("pick", "reject", "none")  # "none" = unflagged

# Exposure: share of the frame near-white / near-black (quality.clipping). Measured on
# the first real library: >2% blown in 8% of photos, >5% crushed in 3% (black
# backgrounds are often intentional, hence the looser shadow threshold).
BLOWN, CRUSHED = 0.02, 0.05
EXPOSURE = {
    "highlights": f"p.clip_highlights > {BLOWN}",
    "shadows": f"p.clip_shadows > {CRUSHED}",
    "ok": f"(p.clip_highlights <= {BLOWN} AND p.clip_shadows <= {CRUSHED})",
}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class PhotoFilter:
    tags: list[int] = field(default_factory=list)  # photos must have all of these
    exclude_tags: list[int] = field(default_factory=list)  # ...and none of these
    date_from: str | None = None
    date_to: str | None = None
    camera: list[str] = field(default_factory=list)  # "" means unknown
    lens: list[str] = field(default_factory=list)  # "" means unknown
    ranges: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    orientation: list[str] = field(default_factory=list)
    gps: bool | None = None
    flag: list[str] = field(default_factory=list)  # needs the selections DB attached as "sel"
    exported: bool | None = None  # exported before (selections DB) or not
    country: list[str] = field(default_factory=list)  # location keys (LOCATION_KEYS)
    region: list[str] = field(default_factory=list)
    place: list[str] = field(default_factory=list)
    loc_source: list[str] = field(default_factory=list)
    folder: list[str] = field(default_factory=list)  # parent folder keys (FOLDER_KEY), not subfolders
    ctags: list[int] = field(default_factory=list)  # custom tags (example photos): must be in all
    exclude_ctags: list[int] = field(default_factory=list)  # ...and in none of these
    # Their members, resolved by the server from the embeddings (custom_tags.py):
    ctag_members: list[list[int]] = field(default_factory=list)  # one list per tag in ctags
    ctag_excluded: list[int] = field(default_factory=list)  # union over exclude_ctags
    exposure: list[str] = field(default_factory=list)  # EXPOSURE keys, OR-combined

    def where(self, model_id: str, exclude: frozenset[str] = frozenset()) -> tuple[str, list]:
        """SQL condition over ``photos p`` (only status 'ok'). Facets named in
        ``exclude`` are left out, for counting that facet's own options."""
        clauses = ["p.status = 'ok'"]
        params: list = []

        if self.tags and "tags" not in exclude:
            marks = ",".join("?" * len(self.tags))
            clauses.append(
                f"""p.id IN (SELECT photo_id FROM photo_tags
                    WHERE model_id = ? AND tag_id IN ({marks})
                    GROUP BY photo_id HAVING COUNT(DISTINCT tag_id) = ?)"""
            )
            params += [model_id, *self.tags, len(self.tags)]

        if self.exclude_tags and "tags" not in exclude:
            marks = ",".join("?" * len(self.exclude_tags))
            clauses.append(
                f"""p.id NOT IN (SELECT photo_id FROM photo_tags
                    WHERE model_id = ? AND tag_id IN ({marks}))"""
            )
            params += [model_id, *self.exclude_tags]

        if "tags" not in exclude:
            # One JSON array per tag keeps the query within SQLite's parameter limit.
            for ids in self.ctag_members:
                clauses.append("p.id IN (SELECT value FROM json_each(?))")
                params.append(json.dumps(ids))
            if self.ctag_excluded:
                clauses.append("p.id NOT IN (SELECT value FROM json_each(?))")
                params.append(json.dumps(self.ctag_excluded))

        if "date" not in exclude:
            if self.date_from:
                clauses.append(f"{DATE_EXPR} >= ?")
                params.append(self.date_from)
            if self.date_to:
                clauses.append(f"{DATE_EXPR} <= ?")
                params.append(self.date_to)

        for name, column in (("camera", "p.camera"), ("lens", "p.lens")):
            values = getattr(self, name)
            if not values or name in exclude:
                continue
            parts = []
            named = [v for v in values if v]
            if named:
                parts.append(f"{column} IN ({','.join('?' * len(named))})")
                params += named
            if "" in values:
                parts.append(f"{column} IS NULL")
            clauses.append("(" + " OR ".join(parts) + ")")

        for name, (lo, hi) in self.ranges.items():
            if name in exclude:
                continue
            column = RANGE_COLUMNS[name]
            if lo is not None:
                clauses.append(f"{column} >= ?")
                params.append(lo)
            if hi is not None:
                clauses.append(f"{column} <= ?")
                params.append(hi)

        if self.orientation and "orientation" not in exclude:
            clauses.append("(" + " OR ".join(ORIENTATIONS[o] for o in self.orientation) + ")")

        if self.gps is not None and "gps" not in exclude:
            clauses.append("p.lat IS NOT NULL" if self.gps else "p.lat IS NULL")

        for level, expr in LOCATION_KEYS.items():
            keys = getattr(self, level)
            if keys and level not in exclude:
                clauses.append(f"{expr} IN ({','.join('?' * len(keys))})")
                params += keys

        if self.exposure and "exposure" not in exclude:
            clauses.append("(" + " OR ".join(EXPOSURE[e] for e in self.exposure) + ")")

        if self.folder and "folder" not in exclude:
            clauses.append(f"{FOLDER_KEY} IN ({','.join('?' * len(self.folder))})")
            params += self.folder

        if self.loc_source and "loc_source" not in exclude:
            clauses.append(f"{LOCATION_SOURCE} IN ({','.join('?' * len(self.loc_source))})")
            params += self.loc_source

        if self.exported is not None and "exported" not in exclude:
            clauses.append(EXPORTED_EXPR if self.exported else f"NOT {EXPORTED_EXPR}")

        if self.flag and "flag" not in exclude:
            parts = []
            named = [f for f in self.flag if f != "none"]
            if named:
                parts.append(f"{FLAG_EXPR} IN ({','.join('?' * len(named))})")
                params += named
            if "none" in self.flag:
                parts.append(f"{FLAG_EXPR} IS NULL")
            clauses.append("(" + " OR ".join(parts) + ")")

        return " AND ".join(clauses), params


def photo_filter(
    tags: str | None = None,
    exclude_tags: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    camera: list[str] = Query([]),
    lens: list[str] = Query([]),
    focal_min: float | None = None,
    focal_max: float | None = None,
    aperture_min: float | None = None,
    aperture_max: float | None = None,
    iso_min: float | None = None,
    iso_max: float | None = None,
    mp_min: float | None = None,
    mp_max: float | None = None,
    orientation: list[str] = Query([]),
    gps: bool | None = None,
    flag: list[str] = Query([]),
    exported: bool | None = None,
    country: list[str] = Query([]),
    region: list[str] = Query([]),
    place: list[str] = Query([]),
    loc_source: list[str] = Query([]),
    exposure: list[str] = Query([]),
    folder: list[str] = Query([]),
    ctags: str | None = None,
    exclude_ctags: str | None = None,
) -> PhotoFilter:
    """FastAPI dependency: the filter from query parameters."""
    try:
        tag_ids = sorted({int(t) for t in (tags or "").split(",") if t.strip()})
        excluded_ids = sorted({int(t) for t in (exclude_tags or "").split(",") if t.strip()} - set(tag_ids))
        ctag_ids = sorted({int(t) for t in (ctags or "").split(",") if t.strip()})
        excluded_ctag_ids = sorted({int(t) for t in (exclude_ctags or "").split(",") if t.strip()} - set(ctag_ids))
    except ValueError:
        raise HTTPException(400, "tags, exclude_tags, ctags and exclude_ctags must be comma-separated ids")
    for d in (date_from, date_to):
        if d and not _DATE.match(d):
            raise HTTPException(400, "dates must be YYYY-MM-DD")
    bad = [o for o in orientation if o not in ORIENTATIONS]
    if bad:
        raise HTTPException(400, f"orientation must be one of {', '.join(ORIENTATIONS)}")
    if any(e not in EXPOSURE for e in exposure):
        raise HTTPException(400, f"exposure must be one of {', '.join(EXPOSURE)}")
    if any(s not in LOCATION_SOURCES for s in loc_source):
        raise HTTPException(400, f"loc_source must be one of {', '.join(LOCATION_SOURCES)}")
    if any(f not in FLAG_VALUES for f in flag):
        raise HTTPException(400, f"flag must be one of {', '.join(FLAG_VALUES)}")
    ranges = {
        name: (lo, hi)
        for name, lo, hi in (
            ("focal", focal_min, focal_max),
            ("aperture", aperture_min, aperture_max),
            ("iso", iso_min, iso_max),
            ("mp", mp_min, mp_max),
        )
        if lo is not None or hi is not None
    }
    return PhotoFilter(
        tags=tag_ids,
        exclude_tags=excluded_ids,
        date_from=date_from or None,
        date_to=date_to or None,
        camera=list(dict.fromkeys(camera)),
        lens=list(dict.fromkeys(lens)),
        ranges=ranges,
        orientation=list(dict.fromkeys(orientation)),
        gps=gps,
        flag=list(dict.fromkeys(flag)),
        exported=exported,
        country=list(dict.fromkeys(country)),
        region=list(dict.fromkeys(region)),
        place=list(dict.fromkeys(place)),
        loc_source=list(dict.fromkeys(loc_source)),
        exposure=list(dict.fromkeys(exposure)),
        folder=list(dict.fromkeys(folder)),
        ctags=ctag_ids,
        exclude_ctags=excluded_ctag_ids,
    )


def facets(conn: sqlite3.Connection, flt: PhotoFilter, model_id: str) -> dict:
    """Available values for each EXIF filter. Each facet is computed with every
    other active filter applied but not its own, so its options stay selectable."""

    def where(name: str) -> tuple[str, list]:
        return flt.where(model_id, frozenset({name}))

    out: dict = {}

    w, p = where("date")
    lo, hi, n = conn.execute(
        f"SELECT MIN({DATE_EXPR}), MAX({DATE_EXPR}), COUNT(p.taken_at) FROM photos p WHERE {w}", p
    ).fetchone()
    out["date"] = {"min": lo, "max": hi, "count": n}

    for name, column in (("camera", "p.camera"), ("lens", "p.lens")):
        w, p = where(name)
        out[name] = [
            {"value": value or "", "count": count}
            for value, count in conn.execute(
                f"""SELECT {column}, COUNT(*) FROM photos p WHERE {w}
                    GROUP BY {column} ORDER BY COUNT(*) DESC, {column}""",
                p,
            )
        ]

    # Each photo's direct parent folder (not its subfolders), in path order.
    w, p = where("folder")
    out["folder"] = [
        {"value": value, "count": count}
        for value, count in conn.execute(
            f"SELECT {FOLDER_KEY} AS k, COUNT(*) FROM photos p WHERE {w} GROUP BY k ORDER BY k", p
        )
    ]

    for name, column in RANGE_COLUMNS.items():
        w, p = where(name)
        lo, hi, n = conn.execute(
            f"SELECT MIN({column}), MAX({column}), COUNT({column}) FROM photos p WHERE {w}", p
        ).fetchone()
        if name == "mp" and n:
            # Shown to one decimal: round outwards, so typing the shown bounds keeps every photo.
            lo, hi = math.floor(lo * 10) / 10, math.ceil(hi * 10) / 10
        out[name] = {"min": lo, "max": hi, "count": n}

    w, p = where("orientation")
    sums = ", ".join(f"COALESCE(SUM({expr}), 0)" for expr in ORIENTATIONS.values())
    row = conn.execute(f"SELECT {sums} FROM photos p WHERE {w}", p).fetchone()
    out["orientation"] = [
        {"value": name, "count": count} for name, count in zip(ORIENTATIONS, row) if count
    ]

    w, p = where("gps")
    with_gps, without = conn.execute(
        f"""SELECT COALESCE(SUM(p.lat IS NOT NULL), 0), COALESCE(SUM(p.lat IS NULL), 0)
            FROM photos p WHERE {w}""",
        p,
    ).fetchone()
    out["gps"] = {"with": with_gps, "without": without}

    w, p = where("flag")
    counts = dict(
        conn.execute(
            f"SELECT COALESCE({FLAG_EXPR}, 'none'), COUNT(*) FROM photos p WHERE {w} GROUP BY 1", p
        ).fetchall()
    )
    out["flag"] = {f: counts.get(f, 0) for f in FLAG_VALUES}

    w, p = where("exported")
    yes, total = conn.execute(f"SELECT COALESCE(SUM({EXPORTED_EXPR}), 0), COUNT(*) FROM photos p WHERE {w}", p).fetchone()
    out["exported"] = {"yes": yes, "no": total - yes}

    w, p = where("exposure")
    sums = ", ".join(f"COALESCE(SUM({expr}), 0)" for expr in EXPOSURE.values())
    out["exposure"] = dict(zip(EXPOSURE, conn.execute(f"SELECT {sums} FROM photos p WHERE {w}", p).fetchone()))

    w, p = where("loc_source")
    counts = dict(conn.execute(f"SELECT {LOCATION_SOURCE}, COUNT(*) FROM photos p WHERE {w} GROUP BY 1", p).fetchall())
    out["loc_source"] = {s: counts.get(s, 0) for s in LOCATION_SOURCES}

    labels = location_labels(conn)
    for level, expr in LOCATION_KEYS.items():
        w, p = where(level)
        out[level] = [
            {"value": key, "label": labels[level].get(key, key), "count": n}
            for key, n in conn.execute(
                f"""SELECT {expr} AS k, COUNT(*) FROM photos p WHERE {w} AND k IS NOT NULL
                    GROUP BY k ORDER BY COUNT(*) DESC, k""",
                p,
            )
        ]
    return out


def location_labels(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Display names for location keys, per level: place "Visby, Gotland", region
    "Gotland, Sweden", country "Sweden". Parts that repeat are left out."""
    out: dict[str, dict[str, str]] = {"country": {}, "region": {}, "place": {}}
    rows = conn.execute(
        "SELECT DISTINCT country_code, country, region, place FROM photo_locations"
    ).fetchall()
    for cc, country, region, place in rows:
        def join(*parts):
            seen = []
            for part in parts:
                if part and part not in seen:
                    seen.append(part)
            return ", ".join(seen) or "Unknown"

        out["country"][cc] = country or cc
        out["region"][f"{cc}|{region}"] = join(region, country)
        out["place"][f"{cc}|{region}|{place}"] = join(place, region, country)
    return out
