"""mimic-tts server — FastAPI app factory."""

from __future__ import annotations

import io
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Any

import soundfile as sf
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from mimic_server.auth import require_token
from mimic_server.backends import TTSBackend, make_backend
from mimic_server.config import Settings

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


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


def _configure_environment(settings: Settings) -> None:
    """Apply environment-level settings (logging, HF_HOME, dirs)."""
    logging.basicConfig(level=settings.log_level)
    if settings.model_cache is not None:
        import os

        os.environ["HF_HOME"] = str(settings.model_cache)
    settings.reference_dir.mkdir(parents=True, exist_ok=True)


def _resolve_clone(settings: Settings, name: str) -> tuple[Any, str]:
    """Look up a registered clone's reference audio path and transcript.
    Raises 400 if not registered."""
    ref_path = settings.reference_dir / name / "audio.wav"
    text_path = settings.reference_dir / name / "text.txt"
    if not (ref_path.exists() and text_path.exists()):
        raise HTTPException(400, f"no voice '{name}' registered")
    return ref_path, text_path.read_text()


def build_app(
    settings: Settings,
    backend_factory: Callable[[Settings], TTSBackend] | None = None,
) -> FastAPI:
    """Construct the FastAPI app with injected settings and backend.

    `backend_factory` is for tests; production uses `make_backend(settings)`
    which dispatches on `settings.backend`.
    """

    _configure_environment(settings)

    backend = (backend_factory or make_backend)(settings)
    auth = Depends(require_token(settings))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        import asyncio

        task = asyncio.create_task(backend.run_lifecycle())
        try:
            yield
        finally:
            task.cancel()
            backend.unload()

    app = FastAPI(title="mimic-tts API", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        on_disk = sorted(
            p.parent.name for p in settings.reference_dir.glob("*/audio.wav")
        )
        return {
            "status": "ok",
            "backend": settings.backend,
            "models_loaded": backend.loaded_keys(),
            "registered_voices": on_disk,
        }

    @app.get("/voices", dependencies=[auth])
    async def list_voices() -> dict[str, list[dict[str, str]]]:
        return {"voices": backend.builtin_voices()}

    @app.get("/clone/voices", dependencies=[auth])
    async def list_clone_voices() -> dict[str, list[str]]:
        on_disk = sorted(
            p.parent.name for p in settings.reference_dir.glob("*/audio.wav")
        )
        return {"voices": on_disk}

    @app.post("/tts", dependencies=[auth])
    async def tts(
        text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
        speaker: Annotated[str, Form()] = "Ryan",
        instruct: Annotated[str, Form()] = "",
    ):
        samples, sr = backend.synth_builtin(
            text=text,
            speaker=speaker,
            language=language,
            instruct=instruct or None,
        )
        return _wav_response(samples, sr)

    @app.post("/clone/register", dependencies=[auth])
    async def clone_register(
        ref_audio: Annotated[UploadFile, File()],
        ref_text: Annotated[str, Form()],
        name: Annotated[str, Form()] = "default",
    ) -> dict[str, str]:
        audio_bytes = await ref_audio.read()
        ref_dir = settings.reference_dir / name
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / "audio.wav").write_bytes(audio_bytes)
        (ref_dir / "text.txt").write_text(ref_text)
        return {"status": "ok", "name": name}

    @app.post("/clone/tts", dependencies=[auth])
    async def clone_tts(
        text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
        name: Annotated[str, Form()] = "default",
    ):
        ref_path, ref_text = _resolve_clone(settings, name)
        samples, sr = backend.synth_clone(
            name=name,
            text=text,
            ref_audio_path=ref_path,
            ref_text=ref_text,
            language=language,
        )
        return _wav_response(samples, sr)

    @app.post("/clone/oneshot", dependencies=[auth])
    async def clone_oneshot(
        text: Annotated[str, Form()],
        ref_audio: Annotated[UploadFile, File()],
        ref_text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
    ):
        audio_bytes = await ref_audio.read()
        samples, sr = backend.synth_clone_oneshot(
            text=text,
            ref_audio_bytes=audio_bytes,
            ref_text=ref_text,
            language=language,
        )
        return _wav_response(samples, sr)

    return app


# Default app for `uvicorn mimic_server.app:app` and the console entry.
app = build_app(Settings())
