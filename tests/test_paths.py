"""Where config, flags and derived data live."""

from pathlib import Path

from riffle import paths
from riffle.config import load_config


def test_first_run_creates_user_config(isolated_user_dirs):
    cfg = load_config()
    home = isolated_user_dirs
    assert cfg.path == home / "config" / "riffle" / "config.yaml"
    assert (home / "config" / "riffle" / "vocabulary.yaml").is_file()  # editable copy
    assert cfg.vocabulary_path == home / "config" / "riffle" / "vocabulary.yaml"
    assert cfg.data_dir == home / "cache" / "riffle"  # derived: disposable
    assert cfg.selections_path == home / "share" / "riffle" / "selections.sqlite3"  # user data
    assert cfg.sources == [] and cfg.location_history == []


def test_config_precedence(isolated_user_dirs, monkeypatch, tmp_path):
    local = isolated_user_dirs / "config.yaml"  # the working directory
    local.write_text("sources: []\n")
    assert paths.find_config() == local.resolve()
    other = tmp_path / "elsewhere.yaml"
    other.write_text("sources: []\n")
    monkeypatch.setenv("RIFFLE_CONFIG", str(other))
    assert paths.find_config() == other.resolve()
    assert paths.find_config(local) == local.resolve()  # --config wins


def test_explicit_locations_and_packaged_vocabulary(tmp_path):
    (tmp_path / "config.yaml").write_text("data_dir: derived\nselections: flags.sqlite3\n")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.data_dir == tmp_path / "derived" and cfg.selections_path == tmp_path / "flags.sqlite3"
    assert cfg.vocabulary_path == paths.default_file("vocabulary.yaml")  # none next to this config
    assert Path(cfg.vocabulary_path).is_file()


def test_standalone_app_starts_with_the_faster_model(isolated_user_dirs, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cfg = load_config()
    assert (cfg.model.name, cfg.model.pretrained) == ("ViT-B-16", "dfn2b")
    assert cfg.stacks.min_similarity == 0.90
    assert "faster model" in cfg.path.read_text()  # says why, in the user's config
