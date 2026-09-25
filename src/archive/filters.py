"""Photo filters shared by listing, search, tag counts and facets.

Tags are AND-combined; values within one facet (cameras, lenses, orientations)
are OR-combined; different facets are AND-combined.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from fastapi import HTTPException, Query

# EXIF dates are stored as written ("2024:05:01 10:00:00"); compare as "2024-05-01".
DATE_EXPR = "replace(substr(p.taken_at, 1, 10), ':', '-')"

# Group keys for the grouped listing: "2026-03-12", "2026-03", "2026"; NULL = undated.
GROUP_KEYS = {
    "day": DATE_EXPR,
    "month": f"substr({DATE_EXPR}, 1, 7)",
    "year": f"substr({DATE_EXPR}, 1, 4)",
}

RANGE_COLUMNS = {"focal": "p.focal_length", "aperture": "p.aperture", "iso": "p.iso"}

ORIENTATIONS = {
    "landscape": "p.width > p.height",
    "portrait": "p.height > p.width",
    "square": "p.width = p.height",
}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class PhotoFilter:
    tags: list[int] = field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None
    camera: list[str] = field(default_factory=list)  # "" means unknown
    lens: list[str] = field(default_factory=list)  # "" means unknown
    ranges: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    orientation: list[str] = field(default_factory=list)
    gps: bool | None = None

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

        return " AND ".join(clauses), params


def photo_filter(
    tags: str | None = None,
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
    orientation: list[str] = Query([]),
    gps: bool | None = None,
) -> PhotoFilter:
    """FastAPI dependency: the filter from query parameters."""
    try:
        tag_ids = sorted({int(t) for t in (tags or "").split(",") if t.strip()})
    except ValueError:
        raise HTTPException(400, "tags must be comma-separated tag ids")
    for d in (date_from, date_to):
        if d and not _DATE.match(d):
            raise HTTPException(400, "dates must be YYYY-MM-DD")
    bad = [o for o in orientation if o not in ORIENTATIONS]
    if bad:
        raise HTTPException(400, f"orientation must be one of {', '.join(ORIENTATIONS)}")
    ranges = {
        name: (lo, hi)
        for name, lo, hi in (
            ("focal", focal_min, focal_max),
            ("aperture", aperture_min, aperture_max),
            ("iso", iso_min, iso_max),
        )
        if lo is not None or hi is not None
    }
    return PhotoFilter(
        tags=tag_ids,
        date_from=date_from or None,
        date_to=date_to or None,
        camera=list(dict.fromkeys(camera)),
        lens=list(dict.fromkeys(lens)),
        ranges=ranges,
        orientation=list(dict.fromkeys(orientation)),
        gps=gps,
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

    for name, column in RANGE_COLUMNS.items():
        w, p = where(name)
        lo, hi, n = conn.execute(
            f"SELECT MIN({column}), MAX({column}), COUNT({column}) FROM photos p WHERE {w}", p
        ).fetchone()
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
    return out
