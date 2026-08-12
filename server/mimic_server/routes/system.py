"""Health, voice listing, and speech-to-text routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import File, HTTPException, UploadFile

from mimic_server.audio import transcode_to_wav
from mimic_server.usage import day_start_iso

if TYPE_CHECKING:
    from fastapi import FastAPI

    from mimic_server.identity import Caller
    from mimic_server.services import Services


def register(app: FastAPI, svc: Services) -> None:
    settings = svc.settings
    backend = svc.backend

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Deliberately anonymous and deliberately uninformative — it is the
        one unauthenticated endpoint, so it must not enumerate voices or
        models."""
        return {
            "status": "ok",
            "backend": settings.backend,
            "stt_enabled": bool(settings.stt_uri),
        }

    # `caller` takes svc.caller as a real default, not via `Annotated[Caller,
    # svc.caller]` — `from __future__ import annotations` stringifies
    # annotations, and FastAPI resolves those strings against this module's
    # globals only, never this closure's `svc`.
    @app.get("/me")
    async def me(caller: Caller = svc.caller) -> dict[str, Any]:
        totals = svc.usage.totals(key_id=caller.id, since=day_start_iso())
        today = totals[0] if totals else {"requests": 0, "chars": 0, "audio_seconds": 0.0}
        return {
            "label": caller.label,
            "role": caller.key.role,
            "can_upload": caller.key.can_upload,
            "max_voices": caller.key.max_voices,
            "voices_used": svc.voices.count_owned(caller.id),
            "daily_char_quota": caller.key.daily_char_quota,
            "usage_today": {
                "requests": today["requests"],
                "chars": today["chars"],
                "audio_seconds": today["audio_seconds"],
            },
            "models_loaded": backend.loaded_keys() if caller.is_admin else None,
        }

    @app.get("/voices")
    async def list_voices(caller: Caller = svc.caller) -> dict[str, list[dict[str, str]]]:  # noqa: ARG001
        return {"voices": backend.builtin_voices()}

    @app.post("/stt")
    async def stt(
        audio: Annotated[UploadFile, File()],
        caller: Caller = svc.caller,  # noqa: ARG001
    ) -> dict[str, str]:
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
