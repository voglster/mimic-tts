"""The single path every synthesis request takes: quota, resolve, synth, record."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mimic_server.identity import Caller
    from mimic_server.services import Services


def synthesize(
    svc: Services,
    caller: Caller,
    *,
    endpoint: str,
    text: str,
    voice_spec: str,
    language: str = "English",
    instruct: str | None = None,
) -> tuple[Any, int]:
    svc.usage.check_quota(caller, len(text))

    builtin_names = {v["name"] for v in svc.backend.builtin_voices()}
    if voice_spec in builtin_names:
        voice_id = None
        samples, sample_rate = svc.backend.synth_builtin(
            text=text, speaker=voice_spec, language=language, instruct=instruct
        )
    else:
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
