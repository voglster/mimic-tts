"""Health, voice listing, and speech-to-text routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import File, HTTPException, UploadFile

from mimic_server.audio import transcode_to_wav

if TYPE_CHECKING:
    from fastapi import FastAPI

    from mimic_server.services import Services


def register(app: FastAPI, svc: Services) -> None:
    settings = svc.settings
    backend = svc.backend

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

    @app.get("/voices", dependencies=[svc.auth])
    async def list_voices() -> dict[str, list[dict[str, str]]]:
        return {"voices": backend.builtin_voices()}

    @app.post("/stt", dependencies=[svc.auth])
    async def stt(audio: Annotated[UploadFile, File()]) -> dict[str, str]:
        from mimic_server.stt import STTUnavailableError, transcribe

        if not settings.stt_uri:
            raise HTTPException(503, "STT is not configured (set MIMIC_STT_URI)")
        audio_bytes = await audio.read()
        wav_bytes = transcode_to_wav(audio_bytes, sample_rate=16000)
        try:
            text = await transcribe(wav_bytes, settings.stt_uri)
        except STTUnavailableError as e:
            raise HTTPException(503, str(e)) from e
        except OSError as e:
            raise HTTPException(
                502, f"could not reach STT server at {settings.stt_uri}: {e}"
            ) from e
        return {"text": text}
