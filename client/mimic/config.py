"""Client configuration: kwarg → env → TOML → defaults."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path

DEFAULT_SERVER_URL = "http://localhost:8000"
DEFAULT_VOICE = "default"

_KNOWN_TOML_KEYS = frozenset({"server_url", "token", "default_voice"})


@dataclass
class ClientConfig:
    server_url: str
    token: str | None
    default_voice: str


def _config_dir() -> Path:
    override = os.environ.get("MIMIC_CONFIG_DIR")
    if override:
        return Path(override)
    return user_config_path("mimic", appauthor=False)


def config_file(config_dir: Path | None = None) -> Path:
    """Where the client looks for its TOML config."""
    return (config_dir or _config_dir()) / "config.toml"


def _read_toml(config_dir: Path) -> dict[str, object]:
    path = config_file(config_dir)
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"invalid TOML at {path}: {e}") from e
    return {k: v for k, v in data.items() if k in _KNOWN_TOML_KEYS}


def load_config(
    *,
    server_url: str | None = None,
    token: str | None = None,
    default_voice: str | None = None,
    config_dir: Path | None = None,
) -> ClientConfig:
    """Resolve config: kwarg → env → TOML → defaults."""
    file_data = _read_toml(config_dir or _config_dir())

    resolved_url = (
        server_url
        or os.environ.get("MIMIC_SERVER_URL")
        or file_data.get("server_url")
        or DEFAULT_SERVER_URL
    )
    resolved_token = (
        token
        if token is not None
        else os.environ.get("MIMIC_API_TOKEN") or file_data.get("token") or None
    )
    resolved_voice = default_voice or file_data.get("default_voice") or DEFAULT_VOICE

    return ClientConfig(
        server_url=str(resolved_url),
        token=str(resolved_token) if resolved_token is not None else None,
        default_voice=str(resolved_voice),
    )
