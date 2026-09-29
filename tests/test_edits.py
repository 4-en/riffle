"""Non-destructive edits (edits.py): storage, rendering, the library, bake-in, export."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from riffle import edits


@pytest.fixture
def sel(tmp_path):
    return tmp_path / "share" / "selections.sqlite3"


def picture(path, size=(400, 300)):
    """Left half red, right half blue, a green square at (40..80, 40..80)."""
    a = np.zeros((size[1], size[0], 3), np.uint8)
    a[:, : size[0] // 2] = (200, 30, 30)
    a[:, size[0] // 2 :] = (30, 30, 200)
    a[40:80, 40:80] = (30, 200, 30)
    Image.fromarray(a).save(path)
    return path


def white_patch(w, h):
    return Image.new("RGB", (w, h), (255, 255, 255)), Image.new("L", (w, h), 255)


def test_store_list_enable_delete(sel):
    patch, mask = white_patch(40, 40)
    a = edits.add_edit(sel, "abc", "heal", {"bbox": [40, 40, 40, 40], "size": [400, 300]}, mask, patch)
    g = edits.add_edit(sel, "abc", "geometry", {"crop": [0, 0, 0.5, 1]})
    g2 = edits.add_edit(sel, "abc", "geometry", {"crop": [0.5, 0, 0.5, 1]})  # one frame: replaces the first
    got = edits.list_edits(sel, "abc")
    assert [e.kind for e in got] == ["heal", "geometry"] and got[1].params["crop"] == [0.5, 0, 0.5, 1]
    assert got[0].patch_path.exists() and got[0].mask_path.exists()
    assert edits.edited_shas(sel) == {"abc"}
    edits.set_enabled(sel, a, False)
    assert [e.enabled for e in edits.list_edits(sel, "abc")] == [False, True]
    assert edits.restore(sel, "abc") == 1
    assert edits.edited_shas(sel) == set()
    edits.delete_edit(sel, a)
    assert not got[0].patch_path.exists()
    with pytest.raises(ValueError):
        edits.add_edit(sel, "abc", "heal", {"bbox": [0, 0, 1, 1]})  # a retouch needs its pixels


def test_render_retouch_then_geometry(sel, tmp_path):
    src = picture(tmp_path / "p.png")
    patch, mask = white_patch(40, 40)
    edits.add_edit(sel, "s", "heal", {"bbox": [40, 40, 40, 40], "size": [400, 300]}, mask, patch)
    full = np.asarray(edits.render(src, edits.list_edits(sel, "s")))
    assert (full[60, 60] == 255).all()  # the green square is gone
    # A crop to the left half, then a flip: the patch moves with the frame.
    edits.add_edit(sel, "s", "geometry", {"crop": [0, 0, 0.5, 1], "flip_h": True, "size": [400, 300]})
    out = np.asarray(edits.render(src, edits.list_edits(sel, "s")))
    assert out.shape[:2] == (300, 200)
    assert (out[60, 200 - 1 - 60] == 255).all() and tuple(out[150, 100]) == (200, 30, 30)
    # Turning the retouch off leaves the crop as it was, and the square is back in place.
    heal = edits.list_edits(sel, "s")[0]
    edits.set_enabled(sel, heal.id, False)
    back = np.asarray(edits.render(src, edits.list_edits(sel, "s")))
    assert tuple(back[60, 200 - 1 - 60]) == (30, 200, 30)
    # At preview size the same, scaled.
    small = np.asarray(edits.render(src, edits.list_edits(sel, "s"), max_edge=200))
    assert small.shape[:2] == (150, 100)
    # A quarter turn swaps the sides.
    edits.add_edit(sel, "s", "geometry", {"rot90": 1, "size": [400, 300]})
    assert edits.render(src, edits.list_edits(sel, "s")).size == (300, 400)


def test_render_is_deterministic(sel, tmp_path):
    src = picture(tmp_path / "p.png")
    patch, mask = white_patch(40, 40)
    edits.add_edit(sel, "s", "heal", {"bbox": [40, 40, 40, 40], "size": [400, 300]}, mask, patch)
    edits.add_edit(sel, "s", "geometry", {"angle": 3.0, "crop": [0.1, 0.1, 0.8, 0.8], "size": [400, 300]})
    a = np.asarray(edits.render(src, edits.list_edits(sel, "s")))
    b = np.asarray(edits.render(src, edits.list_edits(sel, "s")))
    assert np.array_equal(a, b)


# ---- the API and the library ---------------------------------------------------------

import base64
import io
import time

from fastapi.testclient import TestClient

from riffle.server import create_app
from conftest import FakeClip, fake_index, photo


@pytest.fixture
def client(indexed):
    with TestClient(create_app(indexed, text_encoder=FakeClip().encode_text, index_runner=fake_index)) as c:
        yield c


def wait(c, path, timeout=30):
    deadline = time.time() + timeout
    while c.get(path).json()["running"]:
        assert time.time() < deadline, f"{path} did not finish"
        time.sleep(0.05)
    status = c.get(path).json()
    assert status["error"] is None, status
    return status


def mask_url(size, box):
    """A transparent PNG with an opaque box: how the editor sends a painted mask."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    im.paste((255, 255, 255, 255), box)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_editing_a_photo(client, indexed, conn):
    c = client
    pid = photo(conn, "IMG_0003.png")["id"]
    info = c.get(f"/api/edits/{pid}").json()
    assert info["edits"] == [] and not info["edited"] and info["size"] == [640, 480]
    preview_before = (indexed.previews_dir / f"{pid}.jpg").read_bytes()

    # Heal a spot: the tool runs in the background, its candidate waits to be kept.
    token = c.post(f"/api/edits/{pid}/run", json={"tool": "heal", "mask": mask_url((320, 240), (100, 100, 120, 120))}).json()["token"]
    result = wait(c, "/api/edits/job")["result"]
    assert result["token"] == token and len(result["candidates"]) == 1
    assert c.get(result["candidates"][0]).status_code == 200
    kept = c.post(f"/api/edits/{pid}/keep", json={"token": token, "index": 0}).json()
    assert kept["edited"] and [e["kind"] for e in kept["edits"]] == ["heal"]
    bx, by, bw, bh = kept["edits"][0]["params"]["bbox"]
    assert bx <= 200 <= bx + bw and by <= 200 <= by + bh  # the mask, scaled to the photo (320 px → 640 px)
    assert c.post(f"/api/edits/{pid}/keep", json={"token": token, "index": 0}).status_code == 404  # kept once

    # The library shows the edited version, the original preview is kept, the measures are redone.
    assert (indexed.previews_dir / f"{pid}.jpg").read_bytes() != preview_before
    assert (indexed.previews_dir / f"{pid}.original.jpg").exists()
    wait(c, "/api/index")
    assert photo(conn, "IMG_0003.png")["phash"] is not None  # re-measured
    item = next(i for i in c.get("/api/photos", params={"dupes": "all"}).json()["items"] if i["id"] == pid)
    assert item["edited"] and "?v=" in item["thumb"]
    assert c.get(f"/thumbs/{pid}.jpg").headers["cache-control"] == "no-cache"

    # A crop, then turning the heal off, then back to the original.
    after = c.post(f"/api/edits/{pid}/geometry", json={"crop": [0, 0, 0.5, 0.5]}).json()
    assert [e["kind"] for e in after["edits"]] == ["heal", "geometry"]
    with Image.open(indexed.previews_dir / f"{pid}.jpg") as im:
        assert im.size == (320, 240)
    heal = after["edits"][0]["id"]
    assert c.post(f"/api/edits/item/{heal}", json={"enabled": False}).json()["ok"]
    restored = c.post(f"/api/edits/{pid}/restore", json={}).json()
    assert not restored["edited"] and restored["disabled"] == 1
    assert not (indexed.previews_dir / f"{pid}.original.jpg").exists()
    with Image.open(indexed.previews_dir / f"{pid}.jpg") as im:
        assert im.size == (640, 480)
    assert c.delete(f"/api/edits/item/{heal}").json()["ok"]
    assert [e["kind"] for e in c.get(f"/api/edits/{pid}").json()["edits"]] == ["geometry"]


def test_keeping_an_edit_while_indexing_holds_the_catalogue(client, indexed, conn):
    """An index run can hold the catalogue's write lock for a while: keeping an edit must
    not wait on it; the run clears the photo's measures itself."""
    import sqlite3

    c = client
    pid = photo(conn, "IMG_0003.png")["id"]
    token = c.post(f"/api/edits/{pid}/run", json={"tool": "heal", "mask": mask_url((320, 240), (100, 100, 120, 120))}).json()["token"]
    wait(c, "/api/edits/job")
    busy = sqlite3.connect(indexed.db_path, timeout=0)
    busy.execute("BEGIN IMMEDIATE")  # what an index step holds while it writes
    try:
        r = c.post(f"/api/edits/{pid}/keep", json={"token": token, "index": 0})
    finally:
        busy.rollback()
        busy.close()
    assert r.status_code == 200 and r.json()["edited"]
    wait(c, "/api/index")
    assert photo(conn, "IMG_0003.png")["phash"] is not None  # cleared by the run, then re-measured


def test_edits_are_shared_by_every_profile(client, indexed, conn):
    c = client
    pid = photo(conn, "IMG_0003.png")["id"]
    c.post(f"/api/edits/{pid}/geometry", json={"flip_h": True})
    slug = c.post("/api/profiles", json={"name": "Other", "copy_from": "default", "parts": ["folders"]}).json()["slug"]
    c.post(f"/api/profiles/{slug}/activate", json={})
    assert c.get(f"/api/edits/{pid}").json()["edited"]


def test_tool_errors(client, conn):
    pid = photo(conn, "IMG_0003.png")["id"]
    assert client.post(f"/api/edits/{pid}/run", json={"tool": "upscale", "mask": mask_url((10, 10), (0, 0, 5, 5))}).status_code == 400
    assert client.post(f"/api/edits/{pid}/geometry", json={"crop": [0, 0, 0, 1]}).status_code == 400
    assert client.get("/api/edits/999999").status_code == 404
    tools = {t["key"]: t for t in client.get("/api/editing/tools").json()["tools"]}
    assert tools["heal"]["available"] and tools["inpaint"]["prompt"] and tools["remove"]["chosen"] == "lama"


def test_baking_edits_into_the_file(client, indexed, conn, archive_dir):
    c = client
    pid = photo(conn, "IMG_0001.jpg")["id"]
    path = archive_dir / "photos" / "trip" / "IMG_0001.jpg"
    before = path.read_bytes()
    old_sha = photo(conn, "IMG_0001.jpg")["sha256"]
    # What the user recorded for the photo, in two profiles.
    c.post("/api/flags", json={"ops": [{"ids": [pid], "flag": "pick"}]})
    c.post("/api/captions", json={"items": [{"id": pid, "caption": "A gate", "tags": ["gate"]}]})
    tag = c.post("/api/custom-tags", json={"name": "Gates", "photo_ids": [pid]}).json()["id"]
    other = c.post("/api/profiles", json={"name": "Other", "copy_from": "default"}).json()["slug"]
    assert c.post(f"/api/edits/{pid}/bake", json={}).status_code == 400  # nothing to bake yet
    c.post(f"/api/edits/{pid}/geometry", json={"crop": [0.25, 0.25, 0.5, 0.5]})
    wait(c, "/api/index")

    done = c.post(f"/api/edits/{pid}/bake", json={"backup": True}).json()
    wait(c, "/api/index")
    assert done["sha256"] != old_sha and path.read_bytes() != before
    assert Path(done["backup"]).read_bytes() == before  # the original, kept
    with Image.open(path) as im:
        assert im.size == (200, 300)  # cropped (the file is 600×400 turned upright by EXIF, then halved)
        assert (im.getexif().get(0x0112) or 1) == 1
    row = photo(conn, "IMG_0001.jpg")
    assert row["id"] == pid and row["sha256"] == done["sha256"] and row["status"] == "ok"
    # Everything recorded moved to the new content, in every profile; the edits are done with.
    item = next(i for i in c.get("/api/photos", params={"dupes": "all"}).json()["items"] if i["id"] == pid)
    assert item["flag"] == "pick" and not item["edited"]
    assert c.get("/api/captions", params={"ids": str(pid)}).json()["captions"][str(pid)]["caption"] == "A gate"
    assert pid in [e for t in c.get("/api/tags").json()["custom"] if t["id"] == tag for e in t["examples"]]
    assert c.get(f"/api/edits/{pid}").json()["edits"] == []
    c.post(f"/api/profiles/{other}/activate", json={})
    assert next(i for i in c.get("/api/photos", params={"dupes": "all"}).json()["items"] if i["id"] == pid)["flag"] == "pick"
    # Without a backup there is none.
    c.post(f"/api/profiles/default/activate", json={})
    c.post(f"/api/edits/{pid}/geometry", json={"flip_h": True})
    wait(c, "/api/index")  # (baking waits for indexing)
    assert c.post(f"/api/edits/{pid}/bake", json={"backup": False}).json()["backup"] is None


def test_export_writes_the_edited_version(client, indexed, conn, tmp_path):
    c = client
    pid = photo(conn, "IMG_0003.png")["id"]
    c.post(f"/api/edits/{pid}/geometry", json={"crop": [0, 0, 0.5, 0.5]})
    wait(c, "/api/index")

    def export(name, **extra):
        out = tmp_path / name
        out.mkdir()
        c.post("/api/export", json={"folder": str(out), "photo_ids": [pid], "add_location": False, **extra})
        result = wait(c, "/api/export")["result"]
        with Image.open(out / "IMG_0003.png") as im:
            return im.size, result

    size, result = export("edited")
    assert size == (320, 240) and result["rendered"] == 1
    size, result = export("originals", originals=True)
    assert size == (640, 480) and result["rendered"] == 0
    assert not (indexed.data_dir / "export-render").exists()  # the renderings are cleaned up


def test_filter_by_edited(client, conn):
    c = client
    pid = photo(conn, "IMG_0003.png")["id"]
    c.post(f"/api/edits/{pid}/geometry", json={"flip_h": True})
    listed = lambda **p: sorted(i["id"] for i in c.get("/api/photos", params={"dupes": "all", **p}).json()["items"])  # noqa: E731
    assert listed(edited="true") == [pid]
    assert pid not in listed(edited="false") and len(listed(edited="false")) == 3
    assert c.get("/api/facets", params={"dupes": "all"}).json()["edited"] == {"yes": 1, "no": 3}


def test_keep_detail_restores_what_the_edit_left_alone():
    """A whole-photo edit is made at a lower resolution: the photo's own fine detail comes
    back where the edit did not change the picture, and not where it did."""
    from riffle.editing import keep_detail

    rng = np.random.default_rng(0)
    a = np.full((400, 600, 3), 120, np.float32) + rng.normal(0, 20, (400, 600, 3))  # fine texture
    original = Image.fromarray(a.clip(0, 255).astype(np.uint8))
    small = np.array(original.resize((150, 100), Image.Resampling.LANCZOS))
    small[:, :75] = (200, 40, 40)  # the edit repaints the left half
    out = np.asarray(keep_detail(original, Image.fromarray(small)), np.float32)
    orig = np.asarray(original, np.float32)
    right, left = (slice(20, 380), slice(330, 580)), (slice(20, 380), slice(20, 270))
    assert np.abs(out[right] - orig[right]).mean() < 3  # the texture is back
    assert np.abs(out[left] - (200, 40, 40)).mean() < 3  # the edit, without the old texture


class FakeInstructPipe:
    """Stands in for Qwen-Image: answers at the size asked for, the top-left quarter red."""

    def __init__(self):
        self.calls = []

    def __call__(self, prompt, image, width, height, num_inference_steps, generator, output_resolution):
        self.calls.append((prompt, image.size, (width, height), num_inference_steps))
        out = image.resize((width, height))
        out.paste((255, 0, 0), (0, 0, out.width // 2, out.height // 2))
        return type("Out", (), {"images": [out]})()


def test_prompt_edit_whole_photo_or_painted_area(client, indexed, conn, monkeypatch):
    from riffle import editing

    pipe = FakeInstructPipe()
    monkeypatch.setattr(editing, "availability", lambda tool, choice=None: {"available": True, "reason": "", "spec": "fake"})
    monkeypatch.setattr(editing, "model_for", lambda tool, choice: ("instruct", pipe))
    c = client
    pid = photo(conn, "IMG_0003.png")["id"]
    tools = {t["key"]: t for t in c.get("/api/editing/tools").json()["tools"]}
    assert tools["instruct"]["mask_optional"] and tools["instruct"]["chosen"] == "qwen-q4"

    # Nothing painted: the whole photo is edited, two candidates to choose from.
    empty = mask_url((320, 240), (0, 0, 0, 0))
    assert c.post(f"/api/edits/{pid}/run", json={"tool": "instruct", "mask": empty, "prompt": " "}).status_code == 400
    c.post(f"/api/edits/{pid}/run", json={"tool": "instruct", "mask": empty, "prompt": "paint it red", "candidates": 2})
    result = wait(c, "/api/edits/job")["result"]
    assert len(result["candidates"]) == 2 and len(pipe.calls) == 2
    # A 4:3 photo is edited at the model's 4:3 shape, about a megapixel.
    assert pipe.calls[0] == ("paint it red", (1184, 896), (1184, 896), editing.STEPS["instruct"])
    kept = c.post(f"/api/edits/{pid}/keep", json={"token": result["token"], "index": 1}).json()
    step = kept["edits"][-1]
    assert step["kind"] == "instruct" and step["params"]["bbox"] == [0, 0, 640, 480] and step["params"]["prompt"] == "paint it red"
    with Image.open(indexed.previews_dir / f"{pid}.jpg") as im:
        assert im.convert("RGB").getpixel((20, 20))[0] > 200  # the edit shows in the library

    # A painted area: only that changes; the model saw it with its surroundings.
    c.post(f"/api/edits/{pid}/restore", json={})
    pipe.calls.clear()
    c.post(f"/api/edits/{pid}/run", json={"tool": "instruct", "mask": mask_url((320, 240), (200, 150, 240, 190)), "prompt": "a red box"})
    result = wait(c, "/api/edits/job")["result"]
    kept = c.post(f"/api/edits/{pid}/keep", json={"token": result["token"], "index": 0}).json()
    x, y, w, h = kept["edits"][-1]["params"]["bbox"]
    assert w < 640 and h < 480 and x <= 400 and x + w >= 480  # around the painted area (×2 to the photo)
    assert pipe.calls[0][1] == (1184, 896)  # the context (at least 1024 px) is all of this small photo



def test_retouch_regions_does_each_area_on_its_own():
    """Things far apart are filled one by one (each with its own context) into one patch."""
    from riffle import editing

    a = np.full((600, 900, 3), 120, np.uint8)
    a[50:90, 60:100] = (250, 0, 0)  # a red square top left…
    a[480:530, 780:830] = (0, 0, 250)  # …and a blue one bottom right
    image = Image.fromarray(a)
    mask = Image.new("L", image.size, 0)
    mask.paste(255, (55, 45, 105, 95))
    mask.paste(255, (775, 475, 835, 535))
    res = editing.retouch_regions("heal", image, mask)
    x, y, w, h = res.bbox
    assert x <= 55 and y <= 45 and x + w >= 835 and y + h >= 535  # one patch over both
    out = image.copy()
    out.paste(res.patch, (x, y), res.mask)
    o = np.asarray(out).astype(int)
    assert abs(o[70, 80] - 120).max() < 25 and abs(o[505, 805] - 120).max() < 25  # both filled from around them
    assert (o[300, 450] == 120).all()  # between them, untouched
    assert editing.retouch_regions("heal", image, Image.new("L", image.size, 0)) is None


def test_find_and_remove_in_many_photos(client, indexed, conn, monkeypatch):
    from riffle import editing, segment

    first, second = photo(conn, "IMG_0003.png")["id"], photo(conn, "IMG_0001.jpg")["id"]
    first_sha = photo(conn, "IMG_0003.png")["sha256"]
    monkeypatch.setattr(editing, "availability", lambda tool, choice=None: {"available": True, "reason": "", "spec": "fake"})
    monkeypatch.setattr(editing, "model_for", lambda tool, choice: ("fake", None))
    calls = []

    def fake_find(loaded, image, text, threshold=0.3, device="cpu"):
        calls.append((text, image.size))
        if len(calls) > 1:
            return []  # nothing in the second photo
        m = np.zeros((image.height, image.width), bool)
        m[40:80, 40:80] = True
        return [segment.Found(m, (40, 40, 80, 80), 0.9, "thing")]

    monkeypatch.setattr(segment, "find", fake_find)
    monkeypatch.setattr(editing, "_fill", lambda loaded, crop, mask: Image.composite(Image.new("RGB", crop.size, "white"), crop, mask))
    c = client
    assert c.post("/api/batch/find", json={"ids": [first], "text": " "}).status_code == 400
    token = c.post("/api/batch/find", json={"ids": [first, second], "text": "things"}).json()["token"]
    found = wait(c, "/api/batch/job")["result"]["items"]
    assert [(i["id"], i["found"]) for i in found] == [(first, 1), (second, 0)]
    assert c.get(found[0]["overlay"]).status_code == 200 and calls[0][0] == "things"
    # The mask the editor paints for Refine: the size of its base image.
    mask = Image.open(io.BytesIO(c.get(found[0]["mask"]).content))
    base = Image.open(io.BytesIO(c.get(f"/api/edits/{first}/base.jpg").content))
    assert mask.size == base.size and mask.getpixel((60, 60)) == 255

    assert c.post("/api/batch/apply", json={"token": "nope", "ids": [first]}).status_code == 404
    assert c.post("/api/batch/apply", json={"token": token, "ids": [first], "tool": "inpaint"}).status_code == 400  # no prompt
    before = (indexed.previews_dir / f"{first}.jpg").read_bytes()
    assert c.post("/api/batch/apply", json={"token": token, "ids": [first, second], "tool": "remove"}).status_code == 200
    done = {i["id"]: i for i in wait(c, "/api/batch/job")["result"]["items"]}
    assert done[first]["applied"] and not done[second].get("applied")
    steps = edits.list_edits(indexed.selections_path, first_sha)
    assert [e.kind for e in steps] == ["remove"] and steps[0].params["find"] == "things"
    assert (indexed.previews_dir / f"{first}.jpg").read_bytes() != before
    assert not c.get(f"/api/edits/{second}").json()["edited"]
    # Undo is deleting the step.
    assert c.delete(f"/api/edits/item/{done[first]['edit_id']}").json()["ok"]
    assert not c.get(f"/api/edits/{first}").json()["edited"]
