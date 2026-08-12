"""Voice-clone registration and permission-aware management routes."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import File, Form, UploadFile
from pydantic import BaseModel

from mimic_server.audio import audio_response, transcode_to_wav

if TYPE_CHECKING:
    from fastapi import FastAPI

    from mimic_server.identity import Caller
    from mimic_server.services import Services


class _VisibilityBody(BaseModel):
    visibility: str


class _GrantBody(BaseModel):
    grantee: str


def register(app: FastAPI, svc: Services) -> None:
    voices = svc.voices
    backend = svc.backend

    # `caller` takes svc.caller as a real default, not via `Annotated[Caller,
    # svc.caller]` — `from __future__ import annotations` stringifies
    # annotations, and FastAPI resolves those strings against this module's
    # globals only, never this closure's `svc`.
    @app.get("/clone/voices")
    async def list_clone_voices(caller: Caller = svc.caller) -> dict[str, Any]:
        visible = voices.visible_to(caller)
        return {
            "voices": [v.qualified for v in visible],
            "detail": [
                {
                    "name": v.name,
                    "qualified": v.qualified,
                    "owner": v.owner_label,
                    "visibility": v.visibility,
                    "mine": v.owner_id == caller.id,
                }
                for v in visible
            ],
        }

    @app.post("/clone/register")
    async def clone_register(
        ref_audio: Annotated[UploadFile, File()],
        ref_text: Annotated[str, Form()],
        caller: Caller = svc.caller,
        name: Annotated[str, Form()] = "default",
    ) -> dict[str, str]:
        wav_bytes = transcode_to_wav(await ref_audio.read())
        voice = voices.register(caller, name, wav_bytes, ref_text)
        # A re-registered name must not keep synthesizing from the old audio.
        with contextlib.suppress(AttributeError, KeyError):
            backend.drop_clone(voice.qualified)  # type: ignore[attr-defined]
        return {"status": "ok", "name": voice.qualified}

    # These two `/grants` routes must be declared before the bare
    # `DELETE /clone/voices/{spec:path}` below: FastAPI/Starlette matches
    # routes in declaration order, and `{spec:path}` greedily matches
    # "warm/grants" or "warm/grants/erin" if it comes first.
    @app.post("/clone/voices/{spec:path}/grants")
    async def clone_grant(
        spec: str, body: _GrantBody, caller: Caller = svc.caller
    ) -> dict[str, str]:
        voices.grant(caller, spec, body.grantee)
        return {"status": "ok"}

    @app.delete("/clone/voices/{spec:path}/grants/{grantee}")
    async def clone_revoke(spec: str, grantee: str, caller: Caller = svc.caller) -> dict[str, str]:
        voices.revoke_grant(caller, spec, grantee)
        return {"status": "ok"}

    @app.patch("/clone/voices/{spec:path}")
    async def clone_set_visibility(
        spec: str, body: _VisibilityBody, caller: Caller = svc.caller
    ) -> dict[str, str]:
        voice = voices.set_visibility(caller, spec, body.visibility)
        return {"status": "ok", "name": voice.qualified, "visibility": voice.visibility}

    @app.delete("/clone/voices/{spec:path}")
    async def clone_delete(spec: str, caller: Caller = svc.caller) -> dict[str, str]:
        voice = voices.delete(caller, spec)
        with contextlib.suppress(AttributeError, KeyError):
            backend.drop_clone(voice.qualified)  # type: ignore[attr-defined]
        return {"status": "ok", "name": voice.qualified}

    @app.post("/clone/tts")
    async def clone_tts(
        text: Annotated[str, Form()],
        caller: Caller = svc.caller,
        language: Annotated[str, Form()] = "English",
        name: Annotated[str, Form()] = "default",
        fmt: Annotated[str, Form(alias="format")] = "wav",
    ):
        voice = voices.resolve(caller, name)
        ref_path, ref_text = voices.reference_paths(voice)
        samples, sr = backend.synth_clone(
            name=voice.qualified,
            text=text,
            ref_audio_path=ref_path,
            ref_text=ref_text,
            language=language,
        )
        return audio_response(samples, sr, fmt=fmt)

    @app.post("/clone/oneshot")
    async def clone_oneshot(
        text: Annotated[str, Form()],
        ref_audio: Annotated[UploadFile, File()],
        ref_text: Annotated[str, Form()],
        caller: Caller = svc.caller,  # noqa: ARG001
        language: Annotated[str, Form()] = "English",
        fmt: Annotated[str, Form(alias="format")] = "wav",
    ):
        wav_bytes = transcode_to_wav(await ref_audio.read())
        samples, sr = backend.synth_clone_oneshot(
            text=text,
            ref_audio_bytes=wav_bytes,
            ref_text=ref_text,
            language=language,
        )
        return audio_response(samples, sr, fmt=fmt)
