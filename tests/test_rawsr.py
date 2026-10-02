"""RAW upscaling (rawsr.py): the parts that need no trained checkpoint, the export and the CLI."""

import argparse
import csv
import struct
from pathlib import Path

import numpy as np
import pytest

from riffle import rawsr
from riffle.cli import _upscale_raw
from riffle.export import ExportError, run_export
from test_export import export, files_in, picked  # noqa: F401 (picked is a fixture)


# ---- black and white -----------------------------------------------------------------------


def test_film_mix_is_the_models_own():
    assert np.allclose(rawsr.mix_gains(rawsr.MONO_MIX), 1)


@pytest.mark.parametrize("name", list(rawsr.FILTERS))
def test_filters_on_film_are_inside_the_trained_range(name):
    gains = rawsr.mix_gains(rawsr.MONO_MIX * np.array(rawsr.FILTERS[name]))
    assert rawsr.MONO_MIX @ gains == pytest.approx(1)  # a neutral grey keeps its brightness


@pytest.mark.parametrize("mix", [[0.5, 0.6, -0.1], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]])
def test_mixes_the_input_cannot_make_are_refused(mix):
    with pytest.raises(rawsr.RawUpscaleError):
        rawsr.mix_gains(mix)


# ---- the input -----------------------------------------------------------------------------


def test_clipped_highlights_turn_neutral():
    packed = np.full((4, 8, 8), 0.3, np.float32)
    packed[:, :2, :2] = [[[0.7]], [[1.0]], [[1.0]], [[0.6]]]  # green clipped, red and blue not
    wb4 = np.array([1.8, 1.0, 1.0, 2.0], np.float32)[:, None, None]
    out = rawsr.clip_highlights(packed, wb4)
    assert np.allclose(out[:, 0, 0], 1.0)  # white, not magenta (R 1.26, G 1.0, B 1.2)
    assert np.allclose(out[:, 7, 7], 0.3 * wb4[:, 0, 0])  # away from it: only white balanced


@pytest.mark.parametrize("flip, shape", [(0, (4, 6)), (3, (4, 6)), (5, (6, 4)), (6, (6, 4))])
def test_upright(flip, shape):
    assert rawsr.upright(np.zeros((4, 6, 3)), flip).shape[:2] == shape


# ---- the DNG -------------------------------------------------------------------------------


def test_dng_carries_the_colour_and_the_raws_exif(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    lin = np.random.default_rng(0).random((6, 8, 3)).astype(np.float32)
    info = {"wb": np.array([2.0, 1.0, 1.5]), "cam_xyz": np.eye(3)}
    path = tmp_path / "out.dng"
    rawsr.write_dng(path, lin, info, "OM Digital Solutions", "OM-5MarkII")
    rawsr.append_exif(path, {33434: (5, 1, struct.pack("<II", 1, 250)), 34855: (3, 1, struct.pack("<H", 400))}, "<")
    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        assert page.photometric == 34892  # LinearRaw
        assert page.tags[50728].value == (500000, 1000000, 1000000, 1000000, 666667, 1000000)  # AsShotNeutral = 1 / wb
        exif = page.tags[34665].value
        assert exif["ExposureTime"] == (1, 250) and exif["ISOSpeedRatings"] == 400
        assert np.abs(page.asarray() / 65535 - lin).max() < 1e-4


# ---- the model -----------------------------------------------------------------------------


def test_availability_names_what_is_missing(cfg, tmp_path):
    cfg.editing = {"raw_upscale_mono": str(tmp_path / "nowhere.pt")}
    r = rawsr.availability(cfg, "mono")
    assert not r["available"] and r["checkpoint"] == str(tmp_path / "nowhere.pt")
    assert "raw extra" in r["reason"] or "no checkpoint" in r["reason"]


def test_checkpoint_kind_is_read_from_it(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    monkeypatch.setattr(rawsr, "_device", lambda: "cpu")
    model = rawsr._build(1)
    # A checkpoint from before the noise input: everything but noise_embed
    state = {k: v for k, v in model.state_dict().items() if not k.startswith("noise_embed.")}
    torch.save(state, tmp_path / "mono.pt")
    rawsr._models.clear()
    loaded, kind, conditioned = rawsr.load(str(tmp_path / "mono.pt"))
    assert (kind, conditioned) == ("mono", False)
    with torch.no_grad():
        assert loaded(torch.rand(1, 4, 16, 24), None).shape == (1, 1, 64, 96)
    rawsr._models.clear()


# ---- the export and the CLI, with the model replaced -----------------------------------------


@pytest.fixture
def fake_model(monkeypatch):
    """rawsr as if a checkpoint were there: upscale_raw writes 1000 bytes and records the call."""
    calls = []
    monkeypatch.setattr(rawsr, "availability", lambda cfg, kind: {"available": True, "reason": "", "checkpoint": "x.pt"})
    monkeypatch.setattr(rawsr, "check", lambda path: "")
    monkeypatch.setattr(rawsr, "expected_bytes", lambda path: 1000)

    def upscale_raw(cfg, src, dst, **kw):
        calls.append((Path(src).name, kw))
        Path(dst).write_bytes(b"D" * 1000)
        return {"size": (8, 6)}

    monkeypatch.setattr(rawsr, "upscale_raw", upscale_raw)
    return calls


def test_export_writes_raws_as_dngs(picked, tmp_path, fake_model):  # noqa: F811
    cfg, ids = picked
    dest = tmp_path / "out"
    summary = export(cfg, ids, dest, content="images_raws", raw_upscale="mono", raw_denoise=0.5, raw_filter="red")
    assert files_in(dest) == ["IMG_0001.dng", "IMG_0001.jpg", "IMG_0003.png", "export-manifest.csv"]
    assert fake_model == [("IMG_0001.CR3", {"kind": "mono", "denoise": 0.5, "base": "film", "filter": "red"})]
    assert summary["raws_upscaled"] == 1 and summary["raws_not_upscaled"] == 0
    with open(dest / "export-manifest.csv", encoding="utf-8-sig") as fh:
        row = next(r for r in csv.DictReader(fh) if r["kind"] == "raw")
    assert row["source"].endswith("IMG_0001.CR3") and row["exported"].endswith("IMG_0001.dng")

    fake_model.clear()  # exported again: the DNG is there, nothing is upscaled twice
    again = export(cfg, ids, dest, content="images_raws", raw_upscale="mono")
    assert fake_model == [] and again["copied"] == 0


def test_export_copies_raws_the_model_cannot_read(picked, tmp_path, fake_model, monkeypatch):  # noqa: F811
    cfg, ids = picked
    monkeypatch.setattr(rawsr, "check", lambda path: "only RGGB Bayer sensors are supported")
    summary = export(cfg, ids, tmp_path / "out", content="raws", raw_upscale="rgb")
    assert "IMG_0001.CR3" in files_in(tmp_path / "out") and fake_model == []
    assert summary["raws_upscaled"] == 0 and summary["raws_not_upscaled"] == 1


def test_export_refuses_raw_upscaling_without_the_model(picked, tmp_path, monkeypatch):  # noqa: F811
    cfg, ids = picked
    monkeypatch.setattr(rawsr, "availability", lambda cfg, kind: {"available": False, "reason": "no checkpoint at x.pt", "checkpoint": "x.pt"})
    with pytest.raises(ExportError, match="no checkpoint"):
        export(cfg, ids, tmp_path / "out", content="raws", raw_upscale="rgb")
    assert not (tmp_path / "out").exists()


def _cli(input, output, **kw):
    args = dict(input=str(input), output=str(output), recursive=False, bw=False, denoise=0.3, luminance=False,
                filter="none", mix=None, overwrite=False)
    return argparse.Namespace(**{**args, **kw})


def test_cli_upscales_a_folder_and_skips_whats_there(cfg, tmp_path, fake_model, capsys):
    raws = tmp_path / "raws"
    (raws / "sub").mkdir(parents=True)
    for name in ("A.ORF", "B.orf", "notes.txt", "sub/C.ORF"):
        (raws / name).write_bytes(b"raw")
    out = tmp_path / "dngs"
    assert _upscale_raw(cfg, _cli(raws, out, bw=True, filter="orange")) == 0
    assert files_in(out) == ["A.dng", "B.dng"]
    assert fake_model[0][1] == {"kind": "mono", "denoise": 0.3, "base": "film", "filter": "orange", "mix": None}

    fake_model.clear()
    assert _upscale_raw(cfg, _cli(raws, out, recursive=True)) == 0
    assert files_in(out) == ["A.dng", "B.dng", "sub/C.dng"] and [c[0] for c in fake_model] == ["C.ORF"]
    assert "already there" in capsys.readouterr().out


def test_cli_one_file_to_a_named_dng(cfg, tmp_path, fake_model):
    (tmp_path / "A.ORF").write_bytes(b"raw")
    assert _upscale_raw(cfg, _cli(tmp_path / "A.ORF", tmp_path / "out" / "big.dng")) == 0
    assert files_in(tmp_path / "out") == ["big.dng"]


def test_cli_refuses_bw_options_for_colour(cfg, tmp_path, fake_model):
    (tmp_path / "A.ORF").write_bytes(b"raw")
    assert _upscale_raw(cfg, _cli(tmp_path / "A.ORF", tmp_path / "out", filter="red")) == 2
    assert fake_model == []
