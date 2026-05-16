"""mimic-tts server — FastAPI app factory."""

from __future__ import annotations

import io
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import soundfile as sf
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
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


def _transcode_to_wav(data: bytes, sample_rate: int = 24000) -> bytes:
    """Convert any audio container ffmpeg understands into mono 16-bit WAV at
    the requested rate. Used to normalize browser uploads (WebM/Opus, MP4/AAC)
    before we hand the file to a backend that only speaks soundfile-readable
    formats.

    WAV input is also accepted (idempotent re-mux), so the CLI client doesn't
    need to know which path it's on. Default rate is 24 kHz (TTS reference);
    pass 16000 for whisper/STT.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                "pipe:1",
            ],
            input=data,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as e:
        raise HTTPException(500, "ffmpeg is not installed on the server") from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if e.stderr else ""
        raise HTTPException(
            400, f"could not decode uploaded audio: {stderr.strip() or 'ffmpeg failed'}"
        ) from e
    return proc.stdout


def build_app(  # noqa: C901 — flat route registration; complexity is incidental
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
            "stt_enabled": bool(settings.stt_uri),
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
        wav_bytes = _transcode_to_wav(audio_bytes)
        ref_dir = settings.reference_dir / name
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / "audio.wav").write_bytes(wav_bytes)
        (ref_dir / "text.txt").write_text(ref_text)
        return {"status": "ok", "name": name}

    @app.delete("/clone/voices/{name}", dependencies=[auth])
    async def clone_delete(name: str) -> dict[str, str]:
        # Block path traversal by rejecting anything that isn't a plain
        # directory name we'd accept on register.
        if "/" in name or "\\" in name or name in {"", ".", ".."}:
            raise HTTPException(400, f"invalid voice name {name!r}")
        ref_dir = settings.reference_dir / name
        if not ref_dir.is_dir():
            raise HTTPException(404, f"no voice {name!r} registered")
        # Drop the loaded model first if the backend has it cached, so the
        # next synth for a re-registered same-name doesn't reuse stale audio.
        try:
            backend.drop_clone(name)  # type: ignore[attr-defined]
        except (AttributeError, KeyError):
            pass
        import shutil

        shutil.rmtree(ref_dir)
        return {"status": "ok", "name": name}

    @app.post("/stt", dependencies=[auth])
    async def stt(audio: Annotated[UploadFile, File()]) -> dict[str, str]:
        from mimic_server.stt import STTUnavailableError, transcribe

        if not settings.stt_uri:
            raise HTTPException(503, "STT is not configured (set MIMIC_STT_URI)")
        audio_bytes = await audio.read()
        wav_bytes = _transcode_to_wav(audio_bytes, sample_rate=16000)
        try:
            text = await transcribe(wav_bytes, settings.stt_uri)
        except STTUnavailableError as e:
            raise HTTPException(503, str(e)) from e
        except OSError as e:
            raise HTTPException(
                502, f"could not reach STT server at {settings.stt_uri}: {e}"
            ) from e
        return {"text": text}

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
        wav_bytes = _transcode_to_wav(audio_bytes)
        samples, sr = backend.synth_clone_oneshot(
            text=text,
            ref_audio_bytes=wav_bytes,
            ref_text=ref_text,
            language=language,
        )
        return _wav_response(samples, sr)

    _mount_web_ui(app)
    return app


def _mount_web_ui(app: FastAPI) -> None:
    """If MIMIC_WEB_DIST points at a built UI directory, serve it at '/'.

    StaticFiles is mounted LAST so the API routes registered above take
    precedence in route matching — `/health`, `/voices`, etc. still resolve
    to the handlers, not to files in the dist tree.
    """
    web_dist = os.environ.get("MIMIC_WEB_DIST", "")
    if not web_dist:
        return
    dist_path = Path(web_dist)
    if not dist_path.is_dir():
        logger.warning("MIMIC_WEB_DIST=%s does not exist; skipping web UI mount", web_dist)
        return
    app.mount("/", StaticFiles(directory=dist_path, html=True), name="web")
    logger.info("serving web UI from %s", dist_path)


# Default app for `uvicorn mimic_server.app:app` and the console entry.
app = build_app(Settings())
