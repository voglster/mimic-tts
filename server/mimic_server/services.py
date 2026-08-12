"""Everything a route module needs, assembled once at app construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mimic_server.backends.base import TTSBackend
    from mimic_server.config import Settings
    from mimic_server.db import Database
    from mimic_server.identity import Key, KeyStore
    from mimic_server.usage import UsageTracker
    from mimic_server.voices import VoiceRegistry


@dataclass
class Services:
    settings: Settings
    backend: TTSBackend
    db: Database
    keys: KeyStore
    voices: VoiceRegistry
    usage: UsageTracker
    root: Key
    caller: Any  # fastapi.Depends(make_caller_dependency(...)) marker
