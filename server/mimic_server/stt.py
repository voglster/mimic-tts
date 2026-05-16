"""Wyoming-protocol STT proxy.

The mimic-tts server doesn't host an ASR model itself — it forwards audio to
an external Wyoming ASR server (e.g. wyoming-faster-whisper) and returns the
transcript. This keeps the dependency surface small and lets the user reuse
whatever ASR container they already have running.
"""

from __future__ import annotations

import io
import logging

import soundfile as sf
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient

logger = logging.getLogger(__name__)

# Whisper expects 16 kHz mono 16-bit PCM. We chunk at this many samples per
# AudioChunk on the wire — same trade-off as the TTS path (small enough that
# the server can pipeline, big enough that we don't flood events).
_TARGET_SAMPLE_RATE = 16000
_CHUNK_SAMPLES = 2048


class STTUnavailableError(RuntimeError):
    """Raised when no STT URI is configured. The /stt route maps this to 503."""


def _parse_uri(uri: str) -> tuple[str, int]:
    """tcp://host:port → (host, port). The Wyoming client accepts the URI
    directly, but we parse here so we can fail fast on malformed config."""
    if not uri.startswith("tcp://"):
        raise ValueError(f"unsupported STT URI scheme: {uri!r} (expected tcp://host:port)")
    rest = uri[len("tcp://") :]
    host, _, port_s = rest.partition(":")
    if not host or not port_s:
        raise ValueError(f"malformed STT URI: {uri!r}")
    return host, int(port_s)


def _wav_to_pcm16(wav_bytes: bytes) -> tuple[bytes, int]:
    """Decode a WAV blob to raw 16-bit PCM at the WAV's native rate.

    The ffmpeg pre-step in app.py is responsible for producing 16 kHz mono in
    the first place; we assert that here rather than resampling a second time.
    """
    buf = io.BytesIO(wav_bytes)
    data, sr = sf.read(buf, dtype="int16", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1).astype("int16")
    return data.tobytes(), int(sr)


async def transcribe(wav_bytes: bytes, uri: str, language: str = "en") -> str:
    """Send a WAV blob (16 kHz mono PCM) to the Wyoming ASR server and return
    the transcript text. Raises STTUnavailable if uri is empty."""
    if not uri:
        raise STTUnavailableError("MIMIC_STT_URI is not configured")

    host, port = _parse_uri(uri)
    pcm, sample_rate = _wav_to_pcm16(wav_bytes)
    if sample_rate != _TARGET_SAMPLE_RATE:
        logger.warning(
            "STT input is %d Hz; whisper expects %d Hz — transcript quality may suffer",
            sample_rate,
            _TARGET_SAMPLE_RATE,
        )

    bytes_per_chunk = _CHUNK_SAMPLES * 2  # int16 mono

    async with AsyncTcpClient(host, port) as client:
        await client.write_event(Transcribe(language=language).event())
        await client.write_event(AudioStart(rate=sample_rate, width=2, channels=1).event())
        for offset in range(0, len(pcm), bytes_per_chunk):
            chunk = pcm[offset : offset + bytes_per_chunk]
            await client.write_event(
                AudioChunk(audio=chunk, rate=sample_rate, width=2, channels=1).event()
            )
        await client.write_event(AudioStop().event())

        # Read events until we get a Transcript. wyoming-faster-whisper sends
        # exactly one Transcript at the end; anything else is a protocol
        # error.
        while True:
            ev = await client.read_event()
            if ev is None:
                raise RuntimeError("STT server closed connection before transcript")
            if Transcript.is_type(ev.type):
                return Transcript.from_event(ev).text
