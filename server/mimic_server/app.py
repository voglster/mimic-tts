"""mimic-tts server — FastAPI app factory."""

from __future__ import annotations

import io
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Any

import soundfile as sf
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

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
    """Apply environment-level settings (logging, HF_HOME, dirs) and enforce
    the public-bind-needs-auth safety check."""
    logging.basicConfig(level=settings.log_level)
    if settings.model_cache is not None:
        import os

        os.environ["HF_HOME"] = str(settings.model_cache)
    settings.reference_dir.mkdir(parents=True, exist_ok=True)
    _check_public_bind_auth(settings)


def _check_public_bind_auth(settings: Settings) -> None:
    """Refuse to start when bound to a non-loopback host without a bearer token.

    Set MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND=1 to override (e.g. when a
    reverse proxy / Tailscale ACL is providing access control upstream).
    """
    is_loopback = settings.host in {"127.0.0.1", "::1", "localhost"}
    if is_loopback or settings.api_token or settings.allow_unauthenticated_public_bind:
        if settings.api_token:
            logger.info("bearer auth ON (MIMIC_API_TOKEN set)")
        elif settings.allow_unauthenticated_public_bind:
            logger.warning(
                "auth OFF and host=%s (public). "
                "MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND=1 was set — assuming "
                "upstream access control is enforced.",
                settings.host,
            )
        else:
            logger.info("auth OFF (loopback-only bind)")
        return
    raise RuntimeError(
        f"refusing to start: host={settings.host!r} is publicly reachable but "
        "MIMIC_API_TOKEN is not set. Set MIMIC_API_TOKEN to enable bearer auth, "
        "bind to 127.0.0.1, or set MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND=1 "
        "if access control is enforced upstream (reverse proxy, tailnet ACL)."
    )


def _resolve_clone(settings: Settings, name: str) -> tuple[Any, str]:
    """Look up a registered clone's reference audio path and transcript.
    Raises 400 if not registered."""
    ref_path = settings.reference_dir / name / "audio.wav"
    text_path = settings.reference_dir / name / "text.txt"
    if not (ref_path.exists() and text_path.exists()):
        raise HTTPException(400, f"no voice '{name}' registered")
    return ref_path, text_path.read_text()


# Audio formats supported by the OpenAI-compatible endpoint. The values are
# (soundfile format, Content-Type). OpenAI also defines mp3/opus/aac, but those
# need an external encoder (ffmpeg / lame) we don't ship — request → 400.
_OPENAI_FORMATS: dict[str, tuple[str, str]] = {
    "wav": ("WAV", "audio/wav"),
    "flac": ("FLAC", "audio/flac"),
    "pcm": ("RAW", "audio/L16"),  # raw 16-bit PCM; OpenAI uses this for low-latency
}


class _OpenAISpeechRequest(BaseModel):
    """Subset of OpenAI's POST /v1/audio/speech body we honor."""

    model: str = "tts-1"  # ignored — we have one engine
    input: str
    voice: str = "default"
    response_format: str = "wav"  # OpenAI default is mp3; we can't encode mp3 yet
    speed: float = 1.0  # ignored — Chatterbox has no native speed knob


def _make_lifespan(backend: TTSBackend, settings: Settings) -> Any:
    """Build the FastAPI lifespan that supervises backend + optional Wyoming."""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        import asyncio

        tasks = [asyncio.create_task(backend.run_lifecycle())]
        if settings.wyoming_enabled:
            from mimic_server.wyoming_server import run_wyoming_server

            tasks.append(asyncio.create_task(run_wyoming_server(backend, settings)))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            backend.unload()

    return lifespan


def _handle_openai_speech(
    req: _OpenAISpeechRequest, backend: TTSBackend, settings: Settings
) -> Response:
    """OpenAI-compatible TTS handler. Routes `voice` to either a built-in or a
    registered clone. Used by HACS `sfortis/openai_tts` and other OpenAI-
    compatible clients (open-webui, etc.)."""
    fmt = req.response_format.lower()
    if fmt not in _OPENAI_FORMATS:
        allowed = ", ".join(_OPENAI_FORMATS)
        detail = (
            f"unsupported response_format {req.response_format!r}; "
            f"supported: {allowed}. (mp3/opus/aac require an encoder we don't ship.)"
        )
        raise HTTPException(400, detail)
    sf_format, content_type = _OPENAI_FORMATS[fmt]

    builtin_names = {v["name"] for v in backend.builtin_voices()}
    if req.voice in builtin_names:
        samples, sr = backend.synth_builtin(text=req.input, speaker=req.voice)
    else:
        ref_path, ref_text = _resolve_clone(settings, req.voice)
        samples, sr = backend.synth_clone(
            name=req.voice,
            text=req.input,
            ref_audio_path=ref_path,
            ref_text=ref_text,
        )

    buf = io.BytesIO()
    sf.write(buf, samples, sr, format=sf_format)
    return Response(content=buf.getvalue(), media_type=content_type)


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
    app = FastAPI(title="mimic-tts API", lifespan=_make_lifespan(backend, settings))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        on_disk = sorted(p.parent.name for p in settings.reference_dir.glob("*/audio.wav"))
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
        on_disk = sorted(p.parent.name for p in settings.reference_dir.glob("*/audio.wav"))
        return {"voices": on_disk}

    @app.post("/tts", dependencies=[auth])
    async def tts(
        text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
        speaker: Annotated[str, Form()] = "default",
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

    @app.post("/v1/audio/speech", dependencies=[auth])
    async def openai_speech(req: _OpenAISpeechRequest) -> Response:
        return _handle_openai_speech(req, backend, settings)

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
