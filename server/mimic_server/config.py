"""Environment-driven settings for the mimic-tts server."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_reference_dir() -> Path:
    data_dir = os.environ.get("MIMIC_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "reference"
    return Path("reference").resolve()


def _default_model_cache() -> Path | None:
    data_dir = os.environ.get("MIMIC_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "models"
    return None


def _default_db_path() -> Path:
    data_dir = os.environ.get("MIMIC_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "mimic.db"
    return Path("mimic.db").resolve()


def _default_host() -> str:
    return "0.0.0.0" if os.environ.get("MIMIC_DATA_DIR") else "127.0.0.1"


class Settings(BaseSettings):
    """All MIMIC_* env vars. Constructed once at app startup."""

    model_config = SettingsConfigDict(env_prefix="MIMIC_", extra="ignore")

    host: str = Field(default_factory=_default_host)
    port: int = 8000
    reference_dir: Path = Field(default_factory=_default_reference_dir)
    model_cache: Path | None = Field(default_factory=_default_model_cache)
    db_path: Path = Field(default_factory=_default_db_path)
    unload_after: int = 0  # 0 = keep model loaded forever; >0 = seconds idle before unload
    api_token: str | None = None
    root_label: str = "root"
    # Wyoming has no auth in-protocol, so it runs as a named key's identity.
    wyoming_key: str = ""
    log_level: str = "INFO"
    backend: str = "chatterbox"
    allow_unauthenticated_public_bind: bool = False  # escape hatch; see app.py
    # Wyoming protocol server (HA-native voice pipeline). Opt-in. Has NO auth —
    # the protocol does not support it. Bind to all interfaces inside the
    # container; the access boundary is the host's port-forward / firewall.
    # Do NOT expose port 10200 to the public internet.
    wyoming_enabled: bool = False
    wyoming_host: str = "0.0.0.0"
    wyoming_port: int = 10200
    # Optional speech-to-text proxy. When set, /stt is enabled and forwards
    # audio uploads to a Wyoming ASR server (e.g. wyoming-faster-whisper).
    # Example: tcp://host.docker.internal:10300
    stt_uri: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def auth_required(self) -> bool:
        return self.api_token is not None
