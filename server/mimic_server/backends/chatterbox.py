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
        model = self._mm.get(MODEL_KEY)
        wav = model.generate(text=text)
        return _to_numpy(wav), int(model.sr)

    def synth_clone(
        self,
        *,
        name: str,  # noqa: ARG002 — Chatterbox is stateless per-call
        text: str,
        ref_audio_path: Path,
        ref_text: str,  # noqa: ARG002 — Chatterbox is zero-shot; transcript not needed
        language: str = "English",  # noqa: ARG002
    ) -> tuple[Any, int]:
        model = self._mm.get(MODEL_KEY)
        wav = model.generate(text=text, audio_prompt_path=str(ref_audio_path))
        return _to_numpy(wav), int(model.sr)

    def synth_clone_oneshot(
        self,
        *,
        text: str,
        ref_audio_bytes: bytes,
        ref_text: str,  # noqa: ARG002
        language: str = "English",  # noqa: ARG002
    ) -> tuple[Any, int]:
        model = self._mm.get(MODEL_KEY)
        # Chatterbox wants a path; write bytes to a temp file for the call.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(ref_audio_bytes)
            tmp.flush()
            wav = model.generate(text=text, audio_prompt_path=tmp.name)
        return _to_numpy(wav), int(model.sr)

    def loaded_keys(self) -> list[str]:
        return self._mm.loaded_keys()

    def unload(self) -> None:
        self._mm.unload_all()

    async def run_lifecycle(self) -> None:
        await self._mm.run_unload_watcher()
