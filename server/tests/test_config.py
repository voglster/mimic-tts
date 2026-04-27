from pathlib import Path

from mimic_server.config import Settings


def test_local_defaults(monkeypatch, tmp_path):
    for k in ["MIMIC_DATA_DIR", "MIMIC_HOST", "MIMIC_PORT", "MIMIC_REFERENCE_DIR",
              "MIMIC_MODEL_CACHE", "MIMIC_UNLOAD_AFTER", "MIMIC_API_TOKEN"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)

    s = Settings()

    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.reference_dir == Path("reference").resolve()
    assert s.model_cache is None
    assert s.unload_after == 15
    assert s.api_token is None
    assert s.log_level == "INFO"


def test_docker_defaults_when_data_dir_set(monkeypatch):
    monkeypatch.setenv("MIMIC_DATA_DIR", "/data")
    for k in ["MIMIC_HOST", "MIMIC_REFERENCE_DIR", "MIMIC_MODEL_CACHE"]:
        monkeypatch.delenv(k, raising=False)

    s = Settings()

    assert s.host == "0.0.0.0"
    assert s.reference_dir == Path("/data/reference")
    assert s.model_cache == Path("/data/models")


def test_explicit_env_overrides_docker_default(monkeypatch):
    monkeypatch.setenv("MIMIC_DATA_DIR", "/data")
    monkeypatch.setenv("MIMIC_HOST", "10.0.0.5")
    monkeypatch.setenv("MIMIC_REFERENCE_DIR", "/srv/voices")

    s = Settings()

    assert s.host == "10.0.0.5"
    assert s.reference_dir == Path("/srv/voices")


def test_api_token_round_trip(monkeypatch):
    monkeypatch.setenv("MIMIC_API_TOKEN", "shhh")
    s = Settings()
    assert s.api_token == "shhh"
    assert s.auth_required is True


def test_no_token_means_no_auth(monkeypatch):
    monkeypatch.delenv("MIMIC_API_TOKEN", raising=False)
    s = Settings()
    assert s.auth_required is False
