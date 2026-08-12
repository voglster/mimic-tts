"""Built-in-voice synthesis route."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Form

from mimic_server.audio import audio_response

if TYPE_CHECKING:
    from fastapi import FastAPI

    from mimic_server.services import Services


def register(app: FastAPI, svc: Services) -> None:
    @app.post("/tts", dependencies=[svc.auth])
    async def tts(
        text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
        speaker: Annotated[str, Form()] = "default",
        instruct: Annotated[str, Form()] = "",
        fmt: Annotated[str, Form(alias="format")] = "wav",
    ):
        samples, sr = svc.backend.synth_builtin(
            text=text,
            speaker=speaker,
            language=language,
            instruct=instruct or None,
        )
        return audio_response(samples, sr, fmt=fmt)
