"""mimic-tts server — FastAPI app factory."""

from __future__ import annotations

import io
import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Any

import soundfile as sf
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from collections.abc import Callable

from mimic_server.auth import require_token
from mimic_server.config import Settings
from mimic_server.models import ModelManager

logger = logging.getLogger(__name__)

CLONE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
CUSTOM_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"

BUILTIN_VOICES = [
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


def _default_qwen_loader(model_id: str) -> Any:
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


def _on_torch_unload() -> None:
    try:
        import torch

        torch.cuda.empty_cache()
    except ImportError:
        pass


def _wav_response(
    samples: Any, sample_rate: int, filename: str = "output.wav"
) -> StreamingResponse:
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="audio/wav",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def _load_voice_prompt_from_disk(
    model: Any,
    voice_prompts: dict[str, Any],
    name: str,
    reference_dir: Any,
) -> None:
    """Load a voice prompt from disk into the cache, or raise 400 if missing."""
    ref_path = reference_dir / name / "audio.wav"
    ref_text_path = reference_dir / name / "text.txt"
    if not (ref_path.exists() and ref_text_path.exists()):
        raise HTTPException(400, f"no voice '{name}' registered")
    voice_prompts[name] = model.create_voice_clone_prompt(
        ref_audio=str(ref_path),
        ref_text=ref_text_path.read_text(),
    )


def _make_model_manager(
    settings: Settings,
    loader: Callable[[str], Any],
) -> ModelManager[Any]:
    mm: ModelManager[Any] = ModelManager(
        loader=loader,
        unload_after=settings.unload_after,
        on_unload=_on_torch_unload,
    )
    mm.register("clone", CLONE_MODEL_ID)
    mm.register("custom", CUSTOM_MODEL_ID)
    return mm


def _generate_builtin(model: Any, **kwargs: Any) -> tuple[Any, int]:
    """Call generate_custom_voice; map Qwen's ValueError (unsupported speaker) to 400."""
    try:
        return model.generate_custom_voice(**kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _configure_environment(settings: Settings) -> None:
    """Apply environment-level settings (logging, HF_HOME, dirs)."""
    logging.basicConfig(level=settings.log_level)
    if settings.model_cache is not None:
        import os

        os.environ["HF_HOME"] = str(settings.model_cache)
    settings.reference_dir.mkdir(parents=True, exist_ok=True)


def build_app(
    settings: Settings,
    model_loader: Callable[[str], Any] | None = None,
) -> FastAPI:
    """Construct the FastAPI app with injected settings and model loader."""

    _configure_environment(settings)

    loader = model_loader or _default_qwen_loader
    mm = _make_model_manager(settings, loader)

    voice_prompts: dict[str, Any] = {}
    auth = Depends(require_token(settings))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        import asyncio

        task = asyncio.create_task(mm.run_unload_watcher())
        try:
            yield
        finally:
            task.cancel()
            mm.unload_all()

    app = FastAPI(title="mimic-tts API", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "models_loaded": mm.loaded_keys(),
            "registered_voices": list(voice_prompts),
        }

    @app.get("/voices", dependencies=[auth])
    async def list_voices() -> dict[str, list[dict[str, str]]]:
        return {"voices": BUILTIN_VOICES}

    @app.get("/clone/voices", dependencies=[auth])
    async def list_clone_voices() -> dict[str, list[str]]:
        on_disk = {p.parent.name for p in settings.reference_dir.glob("*/audio.wav")}
        return {"voices": sorted(on_disk | voice_prompts.keys())}

    @app.post("/tts", dependencies=[auth])
    async def tts(
        text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
        speaker: Annotated[str, Form()] = "Ryan",
        instruct: Annotated[str, Form()] = "",
    ):
        model = mm.get("custom")
        wavs, sr = _generate_builtin(
            model,
            text=text,
            language=language,
            speaker=speaker,
            instruct=instruct or None,
        )
        return _wav_response(wavs[0], sr)

    @app.post("/clone/register", dependencies=[auth])
    async def clone_register(
        ref_audio: Annotated[UploadFile, File()],
        ref_text: Annotated[str, Form()],
        name: Annotated[str, Form()] = "default",
    ) -> dict[str, str]:
        model = mm.get("clone")
        audio_bytes = await ref_audio.read()
        ref_dir = settings.reference_dir / name
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / "audio.wav").write_bytes(audio_bytes)
        (ref_dir / "text.txt").write_text(ref_text)
        voice_prompts[name] = model.create_voice_clone_prompt(
            ref_audio=str(ref_dir / "audio.wav"),
            ref_text=ref_text,
        )
        return {"status": "ok", "name": name}

    @app.post("/clone/tts", dependencies=[auth])
    async def clone_tts(
        text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
        name: Annotated[str, Form()] = "default",
    ):
        model = mm.get("clone")
        if name not in voice_prompts:
            _load_voice_prompt_from_disk(model, voice_prompts, name, settings.reference_dir)
        wavs, sr = model.generate_voice_clone(
            text=text,
            language=language,
            voice_clone_prompt=voice_prompts[name],
        )
        return _wav_response(wavs[0], sr)

    @app.post("/clone/oneshot", dependencies=[auth])
    async def clone_oneshot(
        text: Annotated[str, Form()],
        ref_audio: Annotated[UploadFile, File()],
        ref_text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
    ):
        model = mm.get("clone")
        audio_bytes = await ref_audio.read()
        wavs, sr = model.generate_voice_clone(
            text=text,
            language=language,
            ref_audio=(io.BytesIO(audio_bytes), None),
            ref_text=ref_text,
        )
        return _wav_response(wavs[0], sr)

    return app


# Default app for `uvicorn mimic_server.app:app` and the console entry.
app = build_app(Settings())
