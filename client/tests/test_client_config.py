import textwrap

import pytest
from mimic.config import ClientConfig, load_config


def test_defaults_only(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMIC_SERVER_URL", raising=False)
    monkeypatch.delenv("MIMIC_API_TOKEN", raising=False)
    cfg = load_config(config_dir=tmp_path)
    assert cfg.server_url == "http://localhost:8000"
    assert cfg.token is None
    assert cfg.default_voice == "default"


def test_env_overrides_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMIC_SERVER_URL", "http://nas.local:8000")
    monkeypatch.setenv("MIMIC_API_TOKEN", "shhh")
    cfg = load_config(config_dir=tmp_path)
    assert cfg.server_url == "http://nas.local:8000"
    assert cfg.token == "shhh"  # noqa: S105


def test_toml_used_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMIC_SERVER_URL", raising=False)
    monkeypatch.delenv("MIMIC_API_TOKEN", raising=False)
    (tmp_path / "config.toml").write_text(
        textwrap.dedent("""
        server_url = "http://nas.local:8000"
        token = "from-toml"
        default_voice = "Aiden"
    """)
    )
    cfg = load_config(config_dir=tmp_path)
    assert cfg.server_url == "http://nas.local:8000"
    assert cfg.token == "from-toml"  # noqa: S105
    assert cfg.default_voice == "Aiden"


def test_env_overrides_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMIC_SERVER_URL", "http://from-env:8000")
    (tmp_path / "config.toml").write_text('server_url = "http://from-toml:8000"\n')
    cfg = load_config(config_dir=tmp_path)
    assert cfg.server_url == "http://from-env:8000"


def test_kwargs_override_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMIC_SERVER_URL", "http://from-env:8000")
    (tmp_path / "config.toml").write_text('server_url = "http://from-toml:8000"\n')
    cfg = load_config(
        server_url="http://from-arg:8000",
        token="from-arg",  # noqa: S106
        config_dir=tmp_path,
    )
    assert cfg.server_url == "http://from-arg:8000"
    assert cfg.token == "from-arg"  # noqa: S105


def test_malformed_toml_raises(tmp_path):
    (tmp_path / "config.toml").write_text("not = valid = toml\n")
    with pytest.raises(ValueError, match="invalid TOML"):
        load_config(config_dir=tmp_path)


def test_unknown_toml_keys_ignored(tmp_path):
    (tmp_path / "config.toml").write_text(
        textwrap.dedent("""
        server_url = "http://x:8000"
        unknown_key = "ignored"
    """)
    )
    cfg = load_config(config_dir=tmp_path)
    assert cfg.server_url == "http://x:8000"


def test_client_config_is_a_dataclass():
    cfg = ClientConfig(server_url="http://x", token=None, default_voice="Ryan")
    assert cfg.server_url == "http://x"
