"""Everything a route module needs, assembled once at app construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Depends

from mimic_server.auth import make_caller_dependency
from mimic_server.bootstrap import bootstrap
from mimic_server.usage import UsageTracker

if TYPE_CHECKING:
    from mimic_server.backends.base import TTSBackend
    from mimic_server.config import Settings
    from mimic_server.db import Database
    from mimic_server.identity import Key, KeyStore
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


def assemble_services(settings: Settings, backend: TTSBackend) -> Services:
    """Run bootstrap and wire up a `Services` bundle.

    The one place this assembly happens — `build_app` and test fixtures both
    call this, instead of each re-deriving `Services` from `Settings` and
    risking the two falling out of sync.
    """
    boot = bootstrap(settings)
    return Services(
        settings=settings,
        backend=backend,
        db=boot.db,
        keys=boot.keys,
        voices=boot.voices,
        usage=UsageTracker(boot.db),
        root=boot.root,
        caller=Depends(make_caller_dependency(settings, boot.keys, boot.root)),
    )
