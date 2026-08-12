"""OpenAI-compatible text-to-speech route."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import soundfile as sf
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

if TYPE_CHECKING:
    from fastapi import FastAPI

    from mimic_server.backends import TTSBackend
    from mimic_server.identity import Caller
    from mimic_server.services import Services
    from mimic_server.voices import VoiceRegistry

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


def _handle_openai_speech(
    req: _OpenAISpeechRequest, caller: Caller, backend: TTSBackend, voices: VoiceRegistry
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
        voice = voices.resolve(caller, req.voice)
        ref_path, ref_text = voices.reference_paths(voice)
        samples, sr = backend.synth_clone(
            name=voice.qualified,
            text=req.input,
            ref_audio_path=ref_path,
            ref_text=ref_text,
        )

    buf = io.BytesIO()
    sf.write(buf, samples, sr, format=sf_format)
    return Response(content=buf.getvalue(), media_type=content_type)


def register(app: FastAPI, svc: Services) -> None:
    # `caller` takes svc.caller as a real default, not via `Annotated[Caller,
    # svc.caller]` — `from __future__ import annotations` stringifies
    # annotations, and FastAPI resolves those strings against this module's
    # globals only, never this closure's `svc`.
    @app.post("/v1/audio/speech")
    async def openai_speech(
        req: _OpenAISpeechRequest,
        caller: Caller = svc.caller,
    ) -> Response:
        return _handle_openai_speech(req, caller, svc.backend, svc.voices)
