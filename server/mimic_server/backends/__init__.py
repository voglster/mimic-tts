"""TTS backend selection.

The backend is chosen by `MIMIC_BACKEND` at startup and owns its own model
lifecycle, caching, and synthesis. The HTTP layer talks to a backend only
through the `TTSBackend` protocol — it never touches engine-specific APIs
directly.

Currently shipped: chatterbox. The abstraction stays in place so additional
engines (Voxtral, etc.) can be wired in without touching HTTP code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mimic_server.backends.base import TTSBackend

if TYPE_CHECKING:
    from mimic_server.config import Settings


def make_backend(settings: Settings) -> TTSBackend:
    name = settings.backend.lower()
    if name == "chatterbox":
        from mimic_server.backends.chatterbox import ChatterboxBackend

        return ChatterboxBackend(settings)
    raise ValueError(f"unknown MIMIC_BACKEND: {settings.backend!r}")


__all__ = ["TTSBackend", "make_backend"]
