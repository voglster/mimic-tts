"""Chatterbox TTS backend (Resemble AI).

Zero-shot voice cloning: every clone synth call passes the reference audio
path directly to `model.generate(audio_prompt_path=...)`. No per-voice
registration step inside the model. Default voice is exposed as a single
built-in named "default" — Chatterbox does not ship named celebrity voices
like Qwen does.
"""

from __future__ import annotations

import logging
import tempfile
import time
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from mimic_server.models import ModelManager

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from mimic_server.config import Settings

logger = logging.getLogger(__name__)

MODEL_KEY = "tts"
DEFAULT_VOICE = "default"


def _default_loader(_model_id: str) -> Any:
    import torch
    from chatterbox.tts import ChatterboxTTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("loading Chatterbox on %s …", device)
    t0 = time.monotonic()
    model = ChatterboxTTS.from_pretrained(device=device)
    logger.info("loaded Chatterbox in %.1fs", time.monotonic() - t0)
    return model


def _empty_cuda_cache() -> None:
    try:
        import torch

        torch.cuda.empty_cache()
    except ImportError:
        pass


def _to_numpy(wav: Any) -> Any:
    """Chatterbox returns a torch tensor shape [1, N]; soundfile wants 1D numpy."""
    try:
        import torch
    except ImportError:
        return wav
    if isinstance(wav, torch.Tensor):
        return wav.squeeze().detach().cpu().numpy()
    return wav


class ChatterboxBackend:
    def __init__(
        self,
        settings: Settings,
        loader: Callable[[str], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._mm: ModelManager[Any] = ModelManager(
            loader=loader or _default_loader,
            unload_after=settings.unload_after,
            on_unload=_empty_cuda_cache,
        )
        self._mm.register(MODEL_KEY, "resemble-ai/chatterbox")

    def _synth(self, **generate_kwargs: Any) -> tuple[Any, int]:
        """Generate speech, then hand the peak CUDA reserve back to the driver.

        Torch's caching allocator keeps every block it has ever taken, and
        Chatterbox's high-water mark scales with utterance length. Without this
        release a single long synthesis pins its peak (~6.6GB observed) for the
        lifetime of the process, starving other models on the same card.
        """
        model = self._mm.get(MODEL_KEY)
        try:
            wav = model.generate(**generate_kwargs)
            return _to_numpy(wav), int(model.sr)
        finally:
            _empty_cuda_cache()

    # ----- TTSBackend protocol -----

    def builtin_voices(self) -> list[dict[str, str]]:
        return [{"name": DEFAULT_VOICE, "language": "English"}]

    def synth_builtin(
        self,
        *,
        text: str,
        speaker: str,
        language: str = "English",  # noqa: ARG002 — Chatterbox infers from text
        instruct: str | None = None,  # noqa: ARG002 — not supported
    ) -> tuple[Any, int]:
        if speaker != DEFAULT_VOICE:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Chatterbox has no built-in voice {speaker!r}. "
                    f"Available: [{DEFAULT_VOICE!r}]. Register a clone instead."
                ),
            )
        return self._synth(text=text)

    def synth_clone(
        self,
        *,
        name: str,  # noqa: ARG002 — Chatterbox is stateless per-call
        text: str,
        ref_audio_path: Path,
        ref_text: str,  # noqa: ARG002 — Chatterbox is zero-shot; transcript not needed
        language: str = "English",  # noqa: ARG002
    ) -> tuple[Any, int]:
        return self._synth(text=text, audio_prompt_path=str(ref_audio_path))

    def synth_clone_oneshot(
        self,
        *,
        text: str,
        ref_audio_bytes: bytes,
        ref_text: str,  # noqa: ARG002
        language: str = "English",  # noqa: ARG002
    ) -> tuple[Any, int]:
        # Chatterbox wants a path; write bytes to a temp file for the call.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(ref_audio_bytes)
            tmp.flush()
            return self._synth(text=text, audio_prompt_path=tmp.name)

    def loaded_keys(self) -> list[str]:
        return self._mm.loaded_keys()

    def unload(self) -> None:
        self._mm.unload_all()

    async def run_lifecycle(self) -> None:
        await self._mm.run_unload_watcher()
