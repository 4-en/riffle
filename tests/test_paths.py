"""Where config, flags and derived data live."""

from pathlib import Path

from archive import paths
from archive.config import load_config


def test_first_run_creates_user_config(isolated_user_dirs):
    cfg = load_config()
    home = isolated_user_dirs
    assert cfg.path == home / "config" / "photo-archive" / "config.yaml"
    assert (home / "config" / "photo-archive" / "vocabulary.yaml").is_file()  # editable copy
    assert cfg.vocabulary_path == home / "config" / "photo-archive" / "vocabulary.yaml"
    assert cfg.data_dir == home / "cache" / "photo-archive"  # derived: disposable
    assert cfg.selections_path == home / "share" / "photo-archive" / "selections.sqlite3"  # user data
    assert cfg.sources == [] and cfg.location_history == []


def test_config_precedence(isolated_user_dirs, monkeypatch, tmp_path):
    local = isolated_user_dirs / "config.yaml"  # the working directory
    local.write_text("sources: []\n")
    assert paths.find_config() == local.resolve()
    other = tmp_path / "elsewhere.yaml"
    other.write_text("sources: []\n")
    monkeypatch.setenv("ARCHIVE_CONFIG", str(other))
    assert paths.find_config() == other.resolve()
    assert paths.find_config(local) == local.resolve()  # --config wins


def test_explicit_locations_and_packaged_vocabulary(tmp_path):
    (tmp_path / "config.yaml").write_text("data_dir: derived\nselections: flags.sqlite3\n")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.data_dir == tmp_path / "derived" and cfg.selections_path == tmp_path / "flags.sqlite3"
    assert cfg.vocabulary_path == paths.default_file("vocabulary.yaml")  # none next to this config
    assert Path(cfg.vocabulary_path).is_file()
