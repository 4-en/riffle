import pytest
from fastapi.testclient import TestClient

from archive.server import create_app
from conftest import photo


@pytest.fixture
def client(indexed, fake_clip, conn):
    # Give the synthetic photos distinct dates, lenses and exposure settings.
    rows = {
        "IMG_0001.jpg": ("2024:05:01 10:00:00", "Canon EOS R5", "RF24-105mm", 50, 4.0, 400),
        "IMG_0002.jpg": ("2024:05:03 09:00:00", "Canon EOS R5", "RF50mm", 50, 1.8, 100),
        "IMG_0002_edit.png": (None, None, None, None, None, None),
        "IMG_0003.png": ("2024:06:10 18:30:00", "OM-5", None, 12, 8.0, 3200),
    }
    for name, (taken, camera, lens, focal, aperture, iso) in rows.items():
        conn.execute(
            """UPDATE photos SET taken_at = ?, camera = ?, lens = ?, focal_length = ?,
               aperture = ?, iso = ? WHERE id = ?""",
            (taken, camera, lens, focal, aperture, iso, photo(conn, name)["id"]),
        )
    conn.commit()
    with TestClient(create_app(indexed, text_encoder=fake_clip.encode_text)) as c:
        c.app_cfg = indexed
        yield c


def names(client, path="/api/photos", **params):
    res = client.get(path, params={"dupes": "all", **params})
    assert res.status_code == 200, res.text
    return sorted(i["rel_path"].rsplit("/", 1)[1] for i in res.json()["items"])


def test_date_range(client):
    assert names(client, date_from="2024-05-02") == ["IMG_0002.jpg", "IMG_0003.png"]
    assert names(client, date_from="2024-05-01", date_to="2024-05-03") == ["IMG_0001.jpg", "IMG_0002.jpg"]
    assert client.get("/api/photos", params={"date_from": "01/05/2024"}).status_code == 400


def test_camera_and_lens_are_or_within_and_across(client):
    assert names(client, lens=["RF50mm", "RF24-105mm"]) == ["IMG_0001.jpg", "IMG_0002.jpg"]
    # "" selects photos without lens data.
    assert names(client, lens=[""]) == ["IMG_0002_edit.png", "IMG_0003.png"]
    assert names(client, camera=["OM-5"], lens=[""]) == ["IMG_0003.png"]


def test_ranges_orientation_and_gps(client):
    assert names(client, iso_min=200) == ["IMG_0001.jpg", "IMG_0003.png"]
    assert names(client, aperture_max=4) == ["IMG_0001.jpg", "IMG_0002.jpg"]
    assert names(client, focal_min=20, focal_max=60) == ["IMG_0001.jpg", "IMG_0002.jpg"]
    assert names(client, orientation=["portrait"]) == ["IMG_0001.jpg"]
    assert names(client, gps="true") == ["IMG_0001.jpg"]
    assert len(names(client, gps="false")) == 3
    assert client.get("/api/photos", params={"orientation": "diagonal"}).status_code == 400


def test_filters_apply_to_search(client, conn):
    a = photo(conn, "IMG_0002.jpg")["id"]
    assert names(client, f"/api/search/similar/{a}", iso_min=200) == ["IMG_0001.jpg", "IMG_0003.png"]
    assert names(client, "/api/search/text", q="anything", lens=[""]) == ["IMG_0002_edit.png", "IMG_0003.png"]


def test_tag_counts_respect_exif_filters(client):
    total = sum(t["count"] for t in client.get("/api/tags").json()["families"]["scene"])
    filtered = sum(t["count"] for t in client.get("/api/tags", params={"iso_min": 200}).json()["families"]["scene"])
    assert (total, filtered) == (4, 2)


def test_facets_exclude_their_own_filter(client):
    f = client.get("/api/facets").json()
    assert f["date"] == {"min": "2024-05-01", "max": "2024-06-10", "count": 3}
    assert {x["value"]: x["count"] for x in f["lens"]} == {"": 2, "RF24-105mm": 1, "RF50mm": 1}
    assert f["iso"] == {"min": 100, "max": 3200, "count": 3}
    assert {x["value"] for x in f["orientation"]} == {"landscape", "portrait"}
    assert f["gps"] == {"with": 1, "without": 3}

    # Selecting a lens narrows the other facets but keeps every lens option.
    f = client.get("/api/facets", params={"lens": "RF50mm"}).json()
    assert len(f["lens"]) == 3
    assert f["iso"] == {"min": 100, "max": 100, "count": 1}
    assert f["camera"] == [{"value": "Canon EOS R5", "count": 1}]


def test_grouped_listing(client):
    kc = lambda groups: [{"key": g["key"], "count": g["count"]} for g in groups]
    res = client.get("/api/photos", params={"group": "day", "dupes": "all"}).json()
    assert kc(res["groups"]) == [
        {"key": "2024-05-01", "count": 1},
        {"key": "2024-05-03", "count": 1},
        {"key": "2024-06-10", "count": 1},
        {"key": "", "count": 1},  # undated last
    ]
    assert [i["group"] for i in res["items"]] == ["2024-05-01", "2024-05-03", "2024-06-10", ""]

    month = client.get("/api/photos", params={"group": "month", "dupes": "all", "sort": "-taken_at"}).json()
    assert [(g["key"], g["count"]) for g in month["groups"]] == [("2024-06", 1), ("2024-05", 2), ("", 1)]
    assert [i["group"] for i in month["items"]] == ["2024-06", "2024-05", "2024-05", ""]

    # Groups follow the filters and duplicate collapsing; empty groups are absent.
    filtered = client.get("/api/photos", params={"group": "year", "iso_min": 200}).json()
    assert kc(filtered["groups"]) == [{"key": "2024", "count": 2}]
    paged = client.get("/api/photos", params={"group": "day", "dupes": "all", "limit": 1, "offset": 3}).json()
    assert len(paged["groups"]) == 4 and paged["items"][0]["group"] == ""

    assert client.get("/api/photos", params={"group": "week"}).status_code == 400
    assert "groups" not in client.get("/api/photos").json()


def test_groups_endpoint_with_covers(client, conn):
    from archive import selections

    res = client.get("/api/groups", params={"group": "month", "dupes": "all"}).json()
    may = next(g for g in res["groups"] if g["key"] == "2024-05")
    ids = {n: photo(conn, n)["id"] for n in ("IMG_0001.jpg", "IMG_0002.jpg")}
    assert may["count"] == 2 and may["cover"] == ids["IMG_0001.jpg"]  # first photo of the month
    assert may["first"] == "2024:05:01 10:00:00" and may["last"] == "2024:05:03 09:00:00"
    # A pick becomes the cover.
    selections.set_flags(conn, client.app_cfg.selections_path, [([ids["IMG_0002.jpg"]], "pick")])
    may = next(g for g in client.get("/api/groups", params={"group": "month", "dupes": "all"}).json()["groups"] if g["key"] == "2024-05")
    assert may["cover"] == ids["IMG_0002.jpg"]
    # Filters apply, and there are no items.
    res = client.get("/api/groups", params={"group": "day", "iso_min": 200}).json()
    assert [g["key"] for g in res["groups"]] == ["2024-05-01", "2024-06-10"] and "items" not in res
    assert client.get("/api/groups", params={"group": "week"}).status_code == 400
