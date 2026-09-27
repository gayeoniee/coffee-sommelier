import importlib

from pipeline import settings


def test_dir_overrides_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NORMALIZED_DIR", str(tmp_path / "n"))
    monkeypatch.setenv("EVAL_DIR", str(tmp_path / "e"))
    importlib.reload(settings)
    assert settings.NORMALIZED_DIR == tmp_path / "n" and settings.EVAL_DIR == tmp_path / "e"
    monkeypatch.delenv("NORMALIZED_DIR")
    monkeypatch.delenv("EVAL_DIR")
    importlib.reload(settings)


def test_paths_are_under_repo_root():
    assert (settings.ROOT / "pyproject.toml").exists()
    assert settings.RAW_DIR == settings.DATA_DIR / "raw"
    assert settings.CURATED_DIR == settings.DATA_DIR / "curated"


def test_database_url_default_is_postgres():
    assert settings.DATABASE_URL.startswith("postgresql://")


def test_load_config_reads_yaml(tmp_path, monkeypatch):
    (tmp_path / "x.yaml").write_text("a: 1\n", encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    assert settings.load_config("x.yaml") == {"a": 1}


def test_tag_ko_extra_ships_with_the_api_image():
    # the Docker image copies only app/, pipeline/, config/ — runtime data files must live under config/
    from pathlib import Path
    from app.core.flavors import load_tag_ko_extra
    root = Path(__file__).resolve().parents[1]
    assert (root / "config" / "tag_ko_extra.yaml").exists()
    assert not (root / "data" / "curated" / "tag_ko_extra.yaml").exists()
    assert "milk chocolate" in load_tag_ko_extra()
    assert "!config/" in (root / ".dockerignore").read_text(encoding="utf-8")
