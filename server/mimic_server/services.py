"""Everything a route module needs, assembled once at app construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mimic_server.backends.base import TTSBackend
    from mimic_server.config import Settings


@dataclass
class Services:
    settings: Settings
    backend: TTSBackend
    auth: Any  # fastapi.Depends(...) marker; replaced by `caller` in Task 4
