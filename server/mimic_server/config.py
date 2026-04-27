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


def _default_host() -> str:
    return "0.0.0.0" if os.environ.get("MIMIC_DATA_DIR") else "127.0.0.1"  # noqa: S104


class Settings(BaseSettings):
    """All MIMIC_* env vars. Constructed once at app startup."""

    model_config = SettingsConfigDict(env_prefix="MIMIC_", extra="ignore")

    host: str = Field(default_factory=_default_host)
    port: int = 8000
    reference_dir: Path = Field(default_factory=_default_reference_dir)
    model_cache: Path | None = Field(default_factory=_default_model_cache)
    unload_after: int = 15
    api_token: str | None = None
    log_level: str = "INFO"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def auth_required(self) -> bool:
        return self.api_token is not None
