"""TTS backend selection.

The backend is chosen by `MIMIC_BACKEND` at startup and owns its own model
lifecycle, caching, and synthesis. The HTTP layer talks to a backend only
through the `TTSBackend` protocol — it never touches Qwen / Chatterbox APIs
directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mimic_server.backends.base import TTSBackend

if TYPE_CHECKING:
    from mimic_server.config import Settings


def make_backend(settings: Settings) -> TTSBackend:
    """Construct the configured backend. Imports are lazy so each backend's
    heavyweight dependencies (torch, qwen-tts, chatterbox) are only loaded
    when actually selected."""
    name = settings.backend.lower()
    if name == "qwen":
        from mimic_server.backends.qwen import QwenBackend

        return QwenBackend(settings)
    if name == "chatterbox":
        from mimic_server.backends.chatterbox import ChatterboxBackend

        return ChatterboxBackend(settings)
    raise ValueError(f"unknown MIMIC_BACKEND: {settings.backend!r}")


__all__ = ["TTSBackend", "make_backend"]
