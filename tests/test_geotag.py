"""Adding GPS to exported copies: lossless, MakerNote-safe, never the originals."""

import io
from pathlib import Path

import pytest
from PIL import Image

from riffle import selections
from riffle.export import run_export
from riffle.geotag import tag
from riffle.raws import match_raws
from riffle.scan import scan, sha256_file
from conftest import _pattern, fake_index, photo

POS = (59.3293, 18.0686, 50.0)


def tagged_bytes(src: Path, t) -> bytes:
    data = src.read_bytes()
    return t.head + data[t.rest :]


def gps_of(data: bytes):
    gps = Image.open(io.BytesIO(data)).getexif().get_ifd(0x8825)
    dms = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
    return gps[1], dms(gps[2]), gps[3], dms(gps[4]), float(gps[31])


def jpeg_with_makernote(path: Path, note: bytes) -> None:
    exif = Image.Exif()
    exif[0x010F] = "Maker"
    exif.get_ifd(0x8769)[0x927C] = note
    _pattern(5).save(path, exif=exif.tobytes(), quality=90)


def test_jpeg_gets_exif_gps_without_reencoding(tmp_path):
    src = tmp_path / "om.jpg"
    jpeg_with_makernote(src, b"OM SYSTEM\x00\x00\x00II\x04\x00" + bytes(40))
    t = tag(src, *POS)
    assert t.method == "exif"
    out = tagged_bytes(src, t)
    ref, lat, lon_ref, lon, acc = gps_of(out)
    assert (ref, lon_ref) == ("N", "E") and lat == pytest.approx(POS[0], abs=1e-5) and lon == pytest.approx(POS[1], abs=1e-5)
    assert acc == 50
    # Everything after the metadata (the compressed image) is byte-identical.
    assert out.endswith(src.read_bytes()[t.rest :]) and t.rest < 200
    assert Image.open(io.BytesIO(out)).getexif().get_ifd(0x8769)[0x927C].startswith(b"OM SYSTEM")
    assert Image.open(io.BytesIO(out)).tobytes() == Image.open(src).tobytes()


def test_existing_exif_bytes_never_move(tmp_path):
    """The GPS IFD is appended: MakerNotes with absolute offsets (e.g. Canon) and the
    EXIF thumbnail stay valid because every original byte keeps its position."""
    src = tmp_path / "canon.jpg"
    jpeg_with_makernote(src, b"\x00\x1a\x00\x01" + bytes(60))  # no header: absolute offsets
    original_tiff = Image.open(src).info["exif"][6:]
    t = tag(src, *POS)
    assert t.method == "exif"
    new_tiff = Image.open(io.BytesIO(tagged_bytes(src, t))).info["exif"][6:]
    assert new_tiff[:4] == original_tiff[:4] and new_tiff[8 : len(original_tiff)] == original_tiff[8:]
    assert gps_of(tagged_bytes(src, t))[1] == pytest.approx(POS[0], abs=1e-5)


def test_exif_too_large_to_grow_falls_back_to_xmp(tmp_path):
    src = tmp_path / "big.jpg"
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9286] = b"ASCII\x00\x00\x00" + b"x" * 65300  # UserComment near the 64 KB limit
    _pattern(5).save(src, exif=exif.tobytes(), quality=90)
    original_exif = Image.open(src).info["exif"]
    t = tag(src, *POS)
    assert t.method == "xmp"
    out = tagged_bytes(src, t)
    assert Image.open(io.BytesIO(out)).info["exif"] == original_exif  # EXIF block untouched
    assert b'exif:GPSLatitude="59,19.758' in out and b"18,4.116" in out


def test_jpeg_without_exif_and_png(tmp_path):
    plain = tmp_path / "plain.jpg"
    _pattern(6).save(plain, quality=90)
    assert gps_of(tagged_bytes(plain, tag(plain, *POS)))[1] == pytest.approx(POS[0], abs=1e-5)

    png = tmp_path / "shot.png"
    _pattern(7).save(png)
    t = tag(png, *POS)
    out = tagged_bytes(png, t)
    assert t.method == "exif" and gps_of(out)[3] == pytest.approx(POS[1], abs=1e-5)
    assert Image.open(io.BytesIO(out)).tobytes() == Image.open(png).tobytes()

    assert tag(tmp_path / "missing.jpg", *POS) is None
    (tmp_path / "raw.orf").write_bytes(b"raw")
    assert tag(tmp_path / "raw.orf", *POS) is None  # RAWs get a sidecar instead


@pytest.fixture
def placed(indexed, conn, archive_dir):
    """IMG_0002 (JPEG, now with a RAW) and IMG_0003 (PNG) placed from a timeline;
    IMG_0001 has camera GPS. All three picked."""
    (archive_dir / "photos" / "trip" / "RAW" / "IMG_0002.CR3").write_bytes(b"raw 2")
    fake_index(indexed)
    ids = {n: photo(conn, n)["id"] for n in ("IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.png")}
    for n in ("IMG_0002.jpg", "IMG_0003.png"):
        conn.execute(
            """INSERT OR REPLACE INTO photo_locations (photo_id, lat, lon, source, accuracy_m)
               VALUES (?, ?, ?, 'visit', ?)""",
            (ids[n], *POS),
        )
    conn.execute(
        "INSERT OR REPLACE INTO photo_locations (photo_id, lat, lon, source, accuracy_m) VALUES (?, 39.9, 116.38, 'exif', 10)",
        (ids["IMG_0001.jpg"],),
    )
    conn.commit()
    selections.set_flags(conn, indexed.selections_path, [(list(ids.values()), "pick")])
    return indexed, list(ids.values())


def test_export_adds_location_to_copies_only(placed, archive_dir, tmp_path):
    cfg, ids = placed
    trip = archive_dir / "photos" / "trip"
    originals = {p: sha256_file(p) for p in trip.rglob("*") if p.is_file()}
    dest = tmp_path / "out"

    summary = run_export(cfg, report=lambda _: None, photo_ids=ids, folder=str(dest), content="images_raws", add_location=True)
    assert {p: sha256_file(p) for p in originals} == originals  # originals untouched
    assert sorted(p.name for p in dest.iterdir()) == [
        "IMG_0001.CR3", "IMG_0001.jpg", "IMG_0002.CR3", "IMG_0002.jpg", "IMG_0002.xmp", "IMG_0003.png", "export-manifest.csv",
    ]
    assert summary["geotagged"] == 2 and summary["sidecars"] == 1
    assert gps_of((dest / "IMG_0002.jpg").read_bytes())[1] == pytest.approx(POS[0], abs=1e-5)
    assert gps_of((dest / "IMG_0003.png").read_bytes())[1] == pytest.approx(POS[0], abs=1e-5)
    assert (dest / "IMG_0002.CR3").read_bytes() == b"raw 2"  # RAW copy unmodified...
    assert b"exif:GPSLatitude" in (dest / "IMG_0002.xmp").read_bytes()  # ...its position is in the sidecar
    assert sha256_file(dest / "IMG_0001.jpg") == originals[trip / "IMG_0001.jpg"]  # camera GPS: untouched
    assert (dest / "IMG_0002.jpg").stat().st_mtime == pytest.approx((trip / "IMG_0002.jpg").stat().st_mtime, abs=1)
    manifest = (dest / "export-manifest.csv").read_text(encoding="utf-8-sig")
    assert ",image," in manifest and "sidecar" in manifest and ",exif" in manifest

    again = run_export(cfg, report=lambda _: None, photo_ids=ids, folder=str(dest), content="images_raws", add_location=True)
    assert (again["copied"], again["skipped"]) == (0, 6)  # resumable: tagged copies are recognised


def test_export_without_location_copies_verbatim(placed, archive_dir, tmp_path):
    cfg, ids = placed
    dest = tmp_path / "plain"
    summary = run_export(cfg, report=lambda _: None, photo_ids=ids, folder=str(dest), add_location=False)
    assert summary["geotagged"] == 0
    for name in ("IMG_0002.jpg", "IMG_0003.png"):
        assert sha256_file(dest / name) == sha256_file(archive_dir / "photos" / "trip" / name)
