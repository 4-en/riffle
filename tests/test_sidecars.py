"""Importing captions, tags and read text from the formats export writes (sidecars.py)."""

import json

import numpy as np
from PIL import Image

from riffle import sidecars
from riffle.geotag import Meta, tag, xmp_packet


def test_xmp_gives_caption_tags_and_read_text():
    packet = xmp_packet(description="A shop front\n\nOFFEN\nOpen", keywords=["shop", "street"], text="OFFEN", translation="Open")
    assert sidecars.parse_xmp(packet) == sidecars.Found("A shop front", ["shop", "street"], "OFFEN", "Open")
    # From before Riffle's own fields: the description is the caption.
    assert sidecars.parse_xmp(xmp_packet(description="A shop front", keywords=["shop"])) == sidecars.Found("A shop front", ["shop"])
    # Only read text, no caption.
    assert sidecars.parse_xmp(xmp_packet(description="OFFEN", text="OFFEN")).caption == ""
    # What cameras write is not a caption.
    assert sidecars.parse_xmp(xmp_packet(description="OLYMPUS DIGITAL CAMERA")).empty()
    assert sidecars.parse_xmp(b"not xmp").empty()


def test_text_files_as_export_writes_them():
    assert sidecars.parse_txt("dog, beach, golden hour\n") == sidecars.Found(tags=["dog", "beach", "golden hour"])
    assert sidecars.parse_txt("A dog runs along the beach.\n") == sidecars.Found(caption="A dog runs along the beach.")
    both = sidecars.parse_txt("A dog runs along the beach.\ndog, beach\nSTOP\nHalt\n")
    assert both == sidecars.Found(caption="A dog runs along the beach.", tags=["dog", "beach"])  # read text is not marked: left out
    assert sidecars.parse_txt("\n").empty()


def test_jsonl_rows():
    row = {"file_name": "a.jpg", "text": "dog, beach", "tags": ["dog", "beach"], "ocr_text": "STOP", "ocr_translation": ""}
    assert sidecars.parse_jsonl_row(row) == sidecars.Found(tags=["dog", "beach"], text="STOP")  # the tags stood in for the caption


def picture(path):
    Image.fromarray(np.full((24, 32, 3), 90, np.uint8)).save(path)
    return path


def test_read_photo_prefers_jsonl_then_sidecar_then_embedded_then_txt(tmp_path):
    a = picture(tmp_path / "a.jpg")
    (tmp_path / "a.txt").write_text("A caption from the text file\nx, y\n", encoding="utf-8")
    assert sidecars.read_photo(a) == sidecars.Found("A caption from the text file", ["x", "y"])
    (tmp_path / "a.xmp").write_bytes(xmp_packet(description="From the sidecar"))
    assert sidecars.read_photo(a) == sidecars.Found("From the sidecar", ["x", "y"])  # tags still from the text file

    # Embedded in a JPEG copy (what export's "embed" writes).
    b = picture(tmp_path / "b.jpg")
    t = tag(b, meta=Meta(description="Embedded", keywords=("k",), text="EXIT", translation="Ausgang"))
    data = b.read_bytes()
    b.write_bytes(t.head + data[t.rest :])
    assert sidecars.read_photo(b) == sidecars.Found("Embedded", ["k"], "EXIT", "Ausgang")

    rows = sidecars._jsonl_rows(tmp_path, [a, b])
    assert rows == {}
    (tmp_path / "metadata.jsonl").write_text(json.dumps({"file_name": "a.jpg", "text": "From JSONL", "tags": ["j"]}) + "\n", encoding="utf-8")
    rows = sidecars._jsonl_rows(tmp_path, [a, b])
    assert sidecars.read_photo(a, rows) == sidecars.Found("From JSONL", ["j"])


def test_darktables_automatic_tags_are_left_out():
    packet = """<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
      <rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:lr="http://ns.adobe.com/lightroom/1.0/">
       <dc:subject><rdf:Bag><rdf:li>darktable</rdf:li><rdf:li>format</rdf:li><rdf:li>jpg</rdf:li><rdf:li>Sweden</rdf:li></rdf:Bag></dc:subject>
       <lr:hierarchicalSubject><rdf:Bag><rdf:li>darktable|format|jpg</rdf:li><rdf:li>places|Sweden</rdf:li></rdf:Bag></lr:hierarchicalSubject>
      </rdf:Description></rdf:RDF></x:xmpmeta>"""
    assert sidecars.parse_xmp(packet).tags == ["Sweden"]
