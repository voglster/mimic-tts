"""Qwen3-TTS backend."""

from __future__ import annotations

import io
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from mimic_server.models import ModelManager

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from mimic_server.config import Settings

logger = logging.getLogger(__name__)

CLONE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
CUSTOM_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"

BUILTIN_VOICES: list[dict[str, str]] = [
    {"name": "Ryan", "language": "English"},
    {"name": "Aiden", "language": "English"},
    {"name": "Vivian", "language": "Chinese"},
    {"name": "Serena", "language": "Chinese"},
    {"name": "Uncle_Fu", "language": "Chinese"},
    {"name": "Dylan", "language": "Chinese"},
    {"name": "Eric", "language": "Chinese"},
    {"name": "Ono_Anna", "language": "Japanese"},
    {"name": "Sohee", "language": "Korean"},
]


def _default_loader(model_id: str) -> Any:
    import torch
    from qwen_tts import Qwen3TTSModel

    logger.info("loading %s …", model_id)
    t0 = time.monotonic()
    model = Qwen3TTSModel.from_pretrained(
        model_id,
        device_map="cuda:0",
        dtype=torch.bfloat16,
    )
    logger.info("loaded %s in %.1fs", model_id, time.monotonic() - t0)
    return model


def _empty_cuda_cache() -> None:
    try:
        import torch

        torch.cuda.empty_cache()
    except ImportError:
        pass


class QwenBackend:
    """Wraps Qwen3-TTS: two models (built-in custom-voice + clone) managed
    with idle unload. Compiled clone prompts cached by name."""

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
        self._mm.register("clone", CLONE_MODEL_ID)
        self._mm.register("custom", CUSTOM_MODEL_ID)
        self._voice_prompts: dict[str, Any] = {}

    # ----- TTSBackend protocol -----

    def builtin_voices(self) -> list[dict[str, str]]:
        return list(BUILTIN_VOICES)

    def synth_builtin(
        self,
        *,
        text: str,
        speaker: str,
        language: str = "English",
        instruct: str | None = None,
    ) -> tuple[Any, int]:
        model = self._mm.get("custom")
        try:
            wavs, sr = model.generate_custom_voice(
                text=text,
                language=language,
                speaker=speaker,
                instruct=instruct or None,
            )
        except ValueError as e:
            # Qwen raises ValueError for unsupported built-in speakers — surface as 400.
            raise HTTPException(status_code=400, detail=str(e)) from e
        return wavs[0], sr

    def synth_clone(
        self,
        *,
        name: str,
        text: str,
        ref_audio_path: Path,
        ref_text: str,
        language: str = "English",
    ) -> tuple[Any, int]:
        model = self._mm.get("clone")
        if name not in self._voice_prompts:
            self._voice_prompts[name] = model.create_voice_clone_prompt(
                ref_audio=str(ref_audio_path),
                ref_text=ref_text,
            )
        wavs, sr = model.generate_voice_clone(
            text=text,
            language=language,
            voice_clone_prompt=self._voice_prompts[name],
        )
        return wavs[0], sr

    def synth_clone_oneshot(
        self,
        *,
        text: str,
        ref_audio_bytes: bytes,
        ref_text: str,
        language: str = "English",
    ) -> tuple[Any, int]:
        model = self._mm.get("clone")
        wavs, sr = model.generate_voice_clone(
            text=text,
            language=language,
            ref_audio=(io.BytesIO(ref_audio_bytes), None),
            ref_text=ref_text,
        )
        return wavs[0], sr

    def loaded_keys(self) -> list[str]:
        return self._mm.loaded_keys()

    def unload(self) -> None:
        self._mm.unload_all()
        self._voice_prompts.clear()

    async def run_lifecycle(self) -> None:
        await self._mm.run_unload_watcher()
