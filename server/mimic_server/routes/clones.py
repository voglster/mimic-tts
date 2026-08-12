"""Voice-clone registration and synthesis routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import File, Form, HTTPException, UploadFile

from mimic_server.audio import audio_response, transcode_to_wav

if TYPE_CHECKING:
    from fastapi import FastAPI

    from mimic_server.config import Settings
    from mimic_server.services import Services


def _resolve_clone(settings: Settings, name: str) -> tuple[Any, str]:
    """Look up a registered clone's reference audio path and transcript.
    Raises 400 if not registered."""
    ref_path = settings.reference_dir / name / "audio.wav"
    text_path = settings.reference_dir / name / "text.txt"
    if not (ref_path.exists() and text_path.exists()):
        raise HTTPException(400, f"no voice '{name}' registered")
    return ref_path, text_path.read_text()


def register(app: FastAPI, svc: Services) -> None:
    settings = svc.settings
    backend = svc.backend

    @app.get("/clone/voices", dependencies=[svc.auth])
    async def list_clone_voices() -> dict[str, list[str]]:
        on_disk = sorted(p.parent.name for p in settings.reference_dir.glob("*/audio.wav"))
        return {"voices": on_disk}

    @app.post("/clone/register", dependencies=[svc.auth])
    async def clone_register(
        ref_audio: Annotated[UploadFile, File()],
        ref_text: Annotated[str, Form()],
        name: Annotated[str, Form()] = "default",
    ) -> dict[str, str]:
        audio_bytes = await ref_audio.read()
        wav_bytes = transcode_to_wav(audio_bytes)
        ref_dir = settings.reference_dir / name
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / "audio.wav").write_bytes(wav_bytes)
        (ref_dir / "text.txt").write_text(ref_text)
        return {"status": "ok", "name": name}

    @app.delete("/clone/voices/{name}", dependencies=[svc.auth])
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

    @app.post("/clone/tts", dependencies=[svc.auth])
    async def clone_tts(
        text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
        name: Annotated[str, Form()] = "default",
        fmt: Annotated[str, Form(alias="format")] = "wav",
    ):
        ref_path, ref_text = _resolve_clone(settings, name)
        samples, sr = backend.synth_clone(
            name=name,
            text=text,
            ref_audio_path=ref_path,
            ref_text=ref_text,
            language=language,
        )
        return audio_response(samples, sr, fmt=fmt)

    @app.post("/clone/oneshot", dependencies=[svc.auth])
    async def clone_oneshot(
        text: Annotated[str, Form()],
        ref_audio: Annotated[UploadFile, File()],
        ref_text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
        fmt: Annotated[str, Form(alias="format")] = "wav",
    ):
        audio_bytes = await ref_audio.read()
        wav_bytes = transcode_to_wav(audio_bytes)
        samples, sr = backend.synth_clone_oneshot(
            text=text,
            ref_audio_bytes=wav_bytes,
            ref_text=ref_text,
            language=language,
        )
        return audio_response(samples, sr, fmt=fmt)
