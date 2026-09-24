from pipeline import settings


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
