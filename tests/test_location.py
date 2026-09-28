"""Location history: parsing, matching photos, place names, grouping, API.

All timelines here are synthetic (Stockholm -> Uppsala); no real history is used."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from riffle import db
from riffle.config import load_config
from riffle.locate import Locator, distance_m, locate_photos
from riffle.server import create_app
from riffle.timeline import HistoryError, load_file, parse_latlng
from conftest import FakeClip, fake_index, photo

STOCKHOLM = (59.3293, 18.0686)
UPPSALA = (59.8586, 17.6389)


def android_timeline():
    """Phone export: a visit in Stockholm 10-11, then a drive to Uppsala 11-12 (UTC+2)."""
    def seg(start, end, **kw):
        return {"startTime": start, "endTime": end, "startTimeTimezoneUtcOffsetMinutes": 120,
                "endTimeTimezoneUtcOffsetMinutes": 120, **kw}
    return {
        "semanticSegments": [
            seg("2024-05-01T10:00:00.000+02:00", "2024-05-01T11:00:00.000+02:00",
                visit={"hierarchyLevel": 0, "probability": 0.9,
                       "topCandidate": {"placeId": "x", "semanticType": "UNKNOWN", "probability": 0.8,
                                        "placeLocation": {"latLng": f"{STOCKHOLM[0]}°, {STOCKHOLM[1]}°"}}}),
            seg("2024-05-01T11:00:00.000+02:00", "2024-05-01T12:00:00.000+02:00",
                activity={"start": {"latLng": f"{STOCKHOLM[0]}°, {STOCKHOLM[1]}°"},
                          "end": {"latLng": f"{UPPSALA[0]}°, {UPPSALA[1]}°"},
                          "distanceMeters": 60000, "topCandidate": {"type": "IN_VEHICLE", "probability": 0.9}}),
            seg("2024-05-01T12:00:00.000+02:00", "2024-05-01T14:00:00.000+02:00",
                timelinePath=[{"point": f"{UPPSALA[0]}°, {UPPSALA[1]}°", "time": "2024-05-01T12:30:00.000+02:00"}]),
        ],
        "rawSignals": [
            {"position": {"LatLng": f"{UPPSALA[0]}°, {UPPSALA[1]}°", "accuracyMeters": 12,
                          "source": "GPS", "timestamp": "2024-05-01T13:00:00.000+02:00"}},
            {"activityRecord": {"probableActivities": [], "timestamp": "2024-05-01T13:00:00.000+02:00"}},
        ],
        "userLocationProfile": {},
    }


@pytest.fixture
def timeline_file(tmp_path):
    path = tmp_path / "Timeline.json"
    path.write_text(json.dumps(android_timeline()))
    return path


# ---- parsing ---------------------------------------------------------------------------


def test_parse_android_export(timeline_file):
    h = load_file(timeline_file)
    assert (len(h.visits), len(h.routes), len(h.points)) == (1, 1, 2)
    assert h.visits[0][2:4] == STOCKHOLM
    assert h.summary() | {"format": "x"} == {
        "format": "x", "from": "2024-05-01", "to": "2024-05-01", "visits": 1, "routes": 1, "points": 2,
    }


def test_parse_ios_records_and_gpx(tmp_path):
    ios = [{"startTime": "2024-05-01T10:00:00.000+02:00", "endTime": "2024-05-01T11:00:00.000+02:00",
            "visit": {"probability": "0.9", "topCandidate": {"placeLocation": f"geo:{STOCKHOLM[0]},{STOCKHOLM[1]}"}}},
           {"startTime": "2024-05-01T11:00:00.000+02:00", "endTime": "2024-05-01T12:00:00.000+02:00",
            "timelinePath": [{"point": f"geo:{UPPSALA[0]},{UPPSALA[1]}", "durationMinutesOffsetFromStartTime": "30"}]}]
    (tmp_path / "ios.json").write_text(json.dumps(ios), encoding="utf-8")
    h = load_file(tmp_path / "ios.json")
    assert len(h.visits) == 1 and h.points[0][0] == load_file(tmp_path / "ios.json").visits[0][1] + 1800

    records = {"locations": [{"latitudeE7": 593293000, "longitudeE7": 180686000, "timestamp": "2024-05-01T08:00:00Z", "accuracy": 20},
                             {"latitudeE7": 598586000, "longitudeE7": 176389000, "timestampMs": "1714554000000"}]}
    (tmp_path / "Records.json").write_text(json.dumps(records), encoding="utf-8")
    assert len(load_file(tmp_path / "Records.json").points) == 2

    gpx = f"""<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
      <trkpt lat="{STOCKHOLM[0]}" lon="{STOCKHOLM[1]}"><time>2024-05-01T08:00:00Z</time></trkpt>
      <trkpt lat="{UPPSALA[0]}" lon="{UPPSALA[1]}"><time>2024-05-01T09:00:00Z</time></trkpt>
    </trkseg></trk></gpx>"""
    (tmp_path / "track.gpx").write_text(gpx, encoding="utf-8")
    assert [p[1:3] for p in load_file(tmp_path / "track.gpx").points] == [STOCKHOLM, UPPSALA]

    (tmp_path / "other.json").write_text('{"hello": 1}', encoding="utf-8")
    with pytest.raises(HistoryError, match="not a recognised"):
        load_file(tmp_path / "other.json")


def test_parse_latlng_forms():
    assert parse_latlng("52.5°, 13.4°") == (52.5, 13.4)
    assert parse_latlng("geo:-33.9,151.2") == (-33.9, 151.2)
    assert parse_latlng({"latLng": "1.5°, -2.5°"}) == (1.5, -2.5)
    assert parse_latlng("999°, 0°") is None and parse_latlng(None) is None


# ---- matching ----------------------------------------------------------------------------


def test_locator_visit_route_gap_and_timezone(timeline_file):
    loc = Locator(load_file(timeline_file), max_gap_s=30 * 60)
    utc = loc.utc("2024:05:01 10:30:00", "+02:00")
    assert utc == loc.utc("2024:05:01 10:30:00", None)  # offset taken from the timeline
    visit = loc.locate(utc)
    assert (visit.source, (visit.lat, visit.lon)) == ("visit", STOCKHOLM)

    route = loc.locate(loc.utc("2024:05:01 11:30:00", "+02:00"))  # halfway through the drive
    mid = ((STOCKHOLM[0] + UPPSALA[0]) / 2, (STOCKHOLM[1] + UPPSALA[1]) / 2)
    assert route.source == "route" and distance_m(route.lat, route.lon, *mid) < 100
    assert route.accuracy_m == 5000  # a 60 km bracket is capped

    near = loc.locate(loc.utc("2024:05:01 13:10:00", "+02:00"))  # 10 min after the last point
    assert near.source == "nearby" and (near.lat, near.lon) == UPPSALA
    assert loc.locate(loc.utc("2024:05:01 16:00:00", "+02:00")) is None  # too far from anything
    assert loc.utc("2024:05:03 10:00:00", None) is None  # no offset known for that day


# ---- pipeline, names, grouping, API --------------------------------------------------------


@pytest.fixture
def located(indexed, conn, timeline_file):
    """IMG_0001 has camera GPS (Beijing); the others are placed from the timeline."""
    for name, taken, tz in [
        ("IMG_0002.jpg", "2024:05:01 10:30:00", "+02:00"),       # visit, Stockholm
        ("IMG_0003.png", "2024:05:01 11:57:00", "+02:00"),       # route, nearly in Uppsala
        ("IMG_0002_edit.png", "2024:05:01 10:40:00", None),     # visit; offset from the timeline
    ]:
        conn.execute("UPDATE photos SET taken_at = ?, tz_offset = ? WHERE id = ?", (taken, tz, photo(conn, name)["id"]))
    conn.commit()
    indexed.location_history = [timeline_file]
    return indexed


def test_locate_photos_and_place_names(located, conn):
    assert locate_photos(conn, located) == {"exif": 1, "visit": 2, "route": 1}
    rows = {r["photo_id"]: r for r in conn.execute("SELECT * FROM photo_locations")}
    beijing = rows[photo(conn, "IMG_0001.jpg")["id"]]
    assert (beijing["source"], beijing["country_code"]) == ("exif", "CN")
    sthlm = rows[photo(conn, "IMG_0002.jpg")["id"]]
    assert (sthlm["country_code"], sthlm["place"], sthlm["region"]) == ("SE", "Stockholm", "Stockholm")
    assert rows[photo(conn, "IMG_0003.png")["id"]]["place"] == "Uppsala"

    assert locate_photos(conn, located) is None  # nothing changed: skipped
    located.location.max_gap_minutes = 10
    assert locate_photos(conn, located) is not None  # settings changed: recomputed


def test_location_grouping_filters_and_detail(located, conn):
    locate_photos(conn, located)
    with TestClient(create_app(located, text_encoder=FakeClip().encode_text)) as c:
        res = c.get("/api/photos", params={"group": "place", "collapse": "none"}).json()
        groups = [(g["label"], g["count"]) for g in res["groups"]]
        # Trip order: Beijing's photo is dated 2024-05-01 10:00, before Stockholm's 10:30.
        assert groups == [("Beijing, China", 1), ("Stockholm, Sweden", 2), ("Uppsala, Sweden", 1)]
        assert res["groups"][1]["first"] == "2024:05:01 10:30:00"
        assert [i["group"] for i in res["items"]] == [g["key"] for g in res["groups"] for _ in range(g["count"])]

        country = c.get("/api/photos", params={"group": "country", "collapse": "none"}).json()["groups"]
        assert [(g["key"], g["label"], g["count"]) for g in country] == [("CN", "China", 1), ("SE", "Sweden", 3)]

        f = c.get("/api/facets").json()
        assert f["loc_source"] == {"exif": 1, "visit": 2, "route": 1, "nearby": 0, "none": 0}
        assert {p["label"] for p in f["place"]} == {"Beijing, China", "Stockholm, Sweden", "Uppsala, Sweden"}
        route = c.get("/api/photos", params={"loc_source": "route", "collapse": "none"}).json()
        assert [i["rel_path"] for i in route["items"]] == ["trip/IMG_0003.png"]
        sweden = c.get("/api/photos", params={"country": "SE", "collapse": "none"}).json()
        assert sweden["total"] == 3
        assert c.get("/api/photos", params={"loc_source": "moon"}).status_code == 400

        d = c.get(f"/api/photos/{photo(conn, 'IMG_0002.jpg')['id']}").json()["location"]
        assert d["source"] == "visit" and d["label"] == "Stockholm, Sweden" and d["country_key"] == "SE"


def test_unknown_location_group_is_last(indexed, conn):
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text)) as c:
        locate_photos(db.connect(indexed.db_path), indexed)
        groups = c.get("/api/photos", params={"group": "place", "collapse": "none"}).json()["groups"]
        assert [(g["key"], g["label"], g["count"]) for g in groups] == [
            ("CN|Beijing|Beijing", "Beijing, China", 1),  # camera GPS only; no timeline configured
            ("", "Unknown location", 3),
        ]


def test_library_location_history_endpoints(archive_dir, timeline_file, tmp_path):
    (archive_dir / "config.yaml").write_text("sources:\n  - photos\nmodel:\n  name: fake\n  pretrained: test\n", encoding="utf-8")
    (archive_dir / "vocabulary.yaml").write_text("subject: [a, b]\n", encoding="utf-8")
    cfg = load_config(archive_dir / "config.yaml")
    with TestClient(create_app(cfg, text_encoder=FakeClip().encode_text, index_runner=fake_index)) as c:
        added = c.post("/api/location-history", json={"path": str(timeline_file)})
        assert added.status_code == 200 and added.json()["visits"] == 1
        assert load_config(archive_dir / "config.yaml").location_history == [timeline_file]
        assert c.post("/api/location-history", json={"path": str(timeline_file)}).status_code == 409
        bad = tmp_path / "notes.json"
        bad.write_text("[1, 2]", encoding="utf-8")
        assert c.post("/api/location-history", json={"path": str(bad)}).status_code == 400

        listed = c.get("/api/location-history").json()
        assert listed["files"][0]["from"] == "2024-05-01" and listed["files"][0]["exists"]

        fs = c.get("/api/fs", params={"path": str(timeline_file.parent), "files": "history"}).json()
        assert "Timeline.json" in [f["name"] for f in fs["files"]]

        while c.get("/api/index").json()["running"]:
            time.sleep(0.05)
        assert c.request("DELETE", "/api/location-history", json={"path": str(timeline_file)}).status_code == 200
        assert load_config(archive_dir / "config.yaml").location_history == []


def test_location_groups_have_map_centres(located, conn):
    locate_photos(conn, located)
    with TestClient(create_app(located, text_encoder=FakeClip().encode_text)) as c:
        groups = c.get("/api/groups", params={"group": "place", "collapse": "none"}).json()["groups"]
        sthlm = next(g for g in groups if g["label"] == "Stockholm, Sweden")
        assert (sthlm["lat"], sthlm["lon"]) == pytest.approx(STOCKHOLM) and sthlm["cover"] is not None
        country = c.get("/api/groups", params={"group": "country", "collapse": "none"}).json()["groups"]
        sweden = next(g for g in country if g["key"] == "SE")
        assert sweden["count"] == 3 and STOCKHOLM[0] < sweden["lat"] < UPPSALA[0]  # centre of its photos
