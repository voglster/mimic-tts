"""Backend protocol — the only interface the HTTP layer depends on."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path


@runtime_checkable
class TTSBackend(Protocol):
    """Synthesis interface. Disk storage for clone references is owned by
    the app layer (reference_dir); backends only read paths handed to them.

    Implementations cache compiled voice prompts internally as they see fit;
    `name` is passed so a backend can key its cache.
    """

    def builtin_voices(self) -> list[dict[str, str]]:
        """List built-in speakers this backend ships with. Empty list = clones only."""

    def synth_builtin(
        self,
        *,
        text: str,
        speaker: str,
        language: str = "English",
        instruct: str | None = None,
    ) -> tuple[Any, int]:
        """Synthesize using a built-in named voice. Returns (samples, sample_rate)."""

    def synth_clone(
        self,
        *,
        name: str,
        text: str,
        ref_audio_path: Path,
        ref_text: str,
        language: str = "English",
    ) -> tuple[Any, int]:
        """Synthesize using a registered clone. `ref_audio_path` and `ref_text`
        are provided every call; backends may cache by `name`."""

    def synth_clone_oneshot(
        self,
        *,
        text: str,
        ref_audio_bytes: bytes,
        ref_text: str,
        language: str = "English",
    ) -> tuple[Any, int]:
        """One-shot clone: synthesize without registering. No caching."""

    def loaded_keys(self) -> list[str]:
        """Names of currently-loaded models, for /health."""

    def unload(self) -> None:
        """Release all loaded models (called on shutdown)."""

    async def run_lifecycle(self) -> None:
        """Long-running task (e.g. idle unload watcher). Cancelled on shutdown."""
