"""Wyoming protocol server — Home Assistant voice-pipeline integration.

Wyoming has no auth (peer-to-peer JSONL + PCM). The trust boundary is the
network: the container binds to all interfaces, and the host firewall /
port-mapping decides who can reach port 10200. Do NOT expose this to the
public internet — use it on a LAN or tailnet.
"""

from __future__ import annotations

import io
import logging
from functools import partial
from typing import TYPE_CHECKING, Any

import numpy as np
import soundfile as sf
from fastapi import HTTPException
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize

if TYPE_CHECKING:
    from wyoming.event import Event

    from mimic_server.backends import TTSBackend
    from mimic_server.config import Settings

logger = logging.getLogger(__name__)

# Wyoming streams 16-bit PCM. We chunk at this many samples per AudioChunk;
# small enough that HA can start playing quickly once we add real streaming,
# big enough that we're not flooding events on the wire for now.
_CHUNK_SAMPLES = 2048


def _build_info(backend: TTSBackend, settings: Settings) -> Info:
    """Construct the Wyoming Info event — our advertised voice catalogue."""
    attribution = Attribution(name="mimic-tts", url="https://github.com/voglster/mimic-tts")
    voices: list[TtsVoice] = [
        TtsVoice(
            name=v["name"],
            description=f"built-in: {v['name']}",
            attribution=attribution,
            installed=True,
            languages=[v.get("language", "en")],
            version=None,
        )
        for v in backend.builtin_voices()
    ]
    voices.extend(
        TtsVoice(
            name=p.parent.name,
            description=f"clone: {p.parent.name}",
            attribution=attribution,
            installed=True,
            languages=["en"],
            version=None,
        )
        for p in sorted(settings.reference_dir.glob("*/audio.wav"))
    )

    return Info(
        tts=[
            TtsProgram(
                name="mimic-tts",
                description=f"mimic-tts ({settings.backend})",
                attribution=attribution,
                installed=True,
                voices=voices,
                version="1",
                supports_synthesize_streaming=False,
            )
        ]
    )


def _samples_to_int16_bytes(samples: Any, sample_rate: int) -> tuple[bytes, int]:
    """Convert backend output to mono 16-bit PCM bytes."""
    # soundfile-roundtrip is the safest way to handle whatever dtype the
    # backend hands us (torch tensor → numpy was done in the backend; might
    # be float32/float64). Write WAV then read back as int16.
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    pcm, sr = sf.read(buf, dtype="int16", always_2d=False)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1).astype(np.int16)
    return pcm.tobytes(), int(sr)


def _route_synth(
    backend: TTSBackend, settings: Settings, text: str, voice_name: str | None
) -> tuple[Any, int]:
    """Route a Wyoming synthesize call to backend builtin-or-clone."""
    name = voice_name or "default"
    builtin_names = {v["name"] for v in backend.builtin_voices()}
    if name in builtin_names:
        return backend.synth_builtin(text=text, speaker=name)
    ref_path = settings.reference_dir / name / "audio.wav"
    text_path = settings.reference_dir / name / "text.txt"
    if not (ref_path.exists() and text_path.exists()):
        raise HTTPException(400, f"no voice '{name}' registered")
    return backend.synth_clone(
        name=name,
        text=text,
        ref_audio_path=ref_path,
        ref_text=text_path.read_text(),
    )


class _MimicHandler(AsyncEventHandler):
    """One handler per Wyoming client connection."""

    def __init__(
        self,
        *args: Any,
        backend: TTSBackend,
        settings: Settings,
        info: Info,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._backend = backend
        self._settings = settings
        self._info = info

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self._info.event())
            return True

        if Synthesize.is_type(event.type):
            synth = Synthesize.from_event(event)
            voice_name = synth.voice.name if synth.voice else None
            try:
                samples, sr = _route_synth(self._backend, self._settings, synth.text, voice_name)
            except HTTPException as e:
                logger.warning("wyoming synth failed: %s", e.detail)
                await self.write_event(Error(text=str(e.detail), code="bad-voice").event())
                return True
            except Exception as e:
                logger.exception("wyoming synth crashed")
                await self.write_event(Error(text=str(e), code="synth-error").event())
                return True

            pcm_bytes, sr = _samples_to_int16_bytes(samples, sr)
            await self.write_event(AudioStart(rate=sr, width=2, channels=1).event())
            # Emit in chunks (still one big batch under the hood — protocol-
            # streamable but content-batched until we add real streaming).
            bytes_per_chunk = _CHUNK_SAMPLES * 2  # 2048 samples * 2 bytes
            for offset in range(0, len(pcm_bytes), bytes_per_chunk):
                chunk = pcm_bytes[offset : offset + bytes_per_chunk]
                await self.write_event(
                    AudioChunk(audio=chunk, rate=sr, width=2, channels=1).event()
                )
            await self.write_event(AudioStop().event())
            return True

        # Unknown event types — keep connection alive.
        return True


async def run_wyoming_server(backend: TTSBackend, settings: Settings) -> None:
    """Start the Wyoming TCP server. Cancellable via task.cancel()."""
    info = _build_info(backend, settings)
    uri = f"tcp://{settings.wyoming_host}:{settings.wyoming_port}"
    logger.info("starting Wyoming server on %s (NO AUTH — keep on tailnet/LAN only)", uri)
    server = AsyncServer.from_uri(uri)
    handler_factory = partial(_MimicHandler, backend=backend, settings=settings, info=info)
    await server.run(handler_factory)
