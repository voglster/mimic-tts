"""The single path every synthesis request takes: quota, resolve, synth, record."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from mimic_server.errors import InvalidRequest, VoiceNotFound

if TYPE_CHECKING:
    from mimic_server.identity import Caller
    from mimic_server.services import Services
    from mimic_server.voices import Voice


def synthesize(
    svc: Services,
    caller: Caller,
    *,
    endpoint: str,
    text: str,
    voice_spec: str,
    language: str = "English",
    instruct: str | None = None,
    prefer_clone: bool = False,
) -> tuple[Any, int]:
    """Resolve `voice_spec` to a built-in or a clone voice and synthesize.

    Built-ins win by default. Pass `prefer_clone=True` (the clone-focused
    routes) to check the registry first and fall back to built-ins only when
    the caller has no matching clone — otherwise a clone named the same as a
    built-in (e.g. "default") would be permanently shadowed on that route.
    """
    svc.usage.check_quota(caller, len(text))

    builtin_names = {v["name"] for v in svc.backend.builtin_voices()}
    voice: Voice | None = None
    if prefer_clone:
        with contextlib.suppress(VoiceNotFound):
            voice = svc.voices.resolve(caller, voice_spec)

    if voice is None and voice_spec in builtin_names:
        voice_id = None
        samples, sample_rate = svc.backend.synth_builtin(
            text=text, speaker=voice_spec, language=language, instruct=instruct
        )
    else:
        if instruct:
            raise InvalidRequest("instruct is only supported for built-in voices")
        if voice is None:
            voice = svc.voices.resolve(caller, voice_spec)
        voice_id = voice.id
        ref_path, ref_text = svc.voices.reference_paths(voice)
        samples, sample_rate = svc.backend.synth_clone(
            name=voice.qualified,
            text=text,
            ref_audio_path=ref_path,
            ref_text=ref_text,
            language=language,
        )

    svc.usage.record(
        caller.id,
        endpoint,
        len(text),
        voice_id=voice_id,
        audio_seconds=len(samples) / sample_rate if sample_rate else 0.0,
    )
    return samples, sample_rate
