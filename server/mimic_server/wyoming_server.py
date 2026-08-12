"""Wyoming protocol server — Home Assistant voice-pipeline integration.

Wyoming carries no credentials (peer-to-peer JSONL + PCM), so every
connection runs as a single, pre-configured key identity (`resolve_wyoming_caller`)
rather than as an authenticated caller. That identity work bounds the blast
radius of a compromised or misconfigured LAN — it does NOT replace the network
boundary. The trust boundary is still the network: the container binds to all
interfaces, and the host firewall / port-mapping decides who can reach port
10200. Do NOT expose this to the public internet — use it on a LAN or tailnet.
"""

from __future__ import annotations

import io
import logging
import re
from functools import partial
from typing import TYPE_CHECKING, Any

import numpy as np
import soundfile as sf
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
)

from mimic_server.errors import QuotaExceeded, VoiceNotFound
from mimic_server.identity import Caller
from mimic_server.synth import synthesize

if TYPE_CHECKING:
    from wyoming.event import Event

    from mimic_server.services import Services

logger = logging.getLogger(__name__)

# Wyoming streams 16-bit PCM. We chunk at this many samples per AudioChunk;
# small enough that HA can start playing quickly, big enough that we're not
# flooding events on the wire.
_CHUNK_SAMPLES = 2048

# Sentence boundary: end-of-sentence punctuation followed by whitespace and a
# capital letter / digit / quote. Conservative — won't split "Mr. Smith".
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+(?=["\']?[A-Z0-9])')

# Coalesce fragments shorter than this into the next chunk — single-word
# synths have setup overhead disproportionate to their audio. Tuned low so
# short greetings ("Hello!", "Sure.") still stream as their own chunk and
# preserve the TTFA win; only truly tiny fragments get merged.
_MIN_SENTENCE_CHARS = 10


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences for incremental synthesis. Coalesces short
    fragments so we don't emit lots of tiny audio chunks with per-call
    overhead. Returns [text] verbatim if no boundaries are found."""
    parts = [p.strip() for p in _SENTENCE_BOUNDARY.split(text.strip()) if p.strip()]
    if not parts:
        return [text.strip()] if text.strip() else []

    out: list[str] = []
    for p in parts:
        if out and len(out[-1]) < _MIN_SENTENCE_CHARS:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


def resolve_wyoming_caller(svc: Services) -> Caller:
    """Resolve the identity Wyoming runs as.

    The protocol carries no credentials, so every connection runs as one
    pre-configured key rather than an authenticated caller. `MIMIC_WYOMING_KEY`
    names that key; an unset or unresolvable label falls back to root, since a
    misconfigured/absent label must not take Home Assistant's TTS offline.
    """
    label = svc.settings.wyoming_key
    if label:
        key = svc.keys.get_by_label(label)
        if key is not None:
            return Caller(key)
        logger.warning("MIMIC_WYOMING_KEY=%r does not match any key; falling back to root", label)
    else:
        logger.info("MIMIC_WYOMING_KEY unset; Wyoming runs as root")
    return Caller(svc.root)


def _build_info(svc: Services, caller: Caller) -> Info:
    """Construct the Wyoming Info event — the voice catalogue visible to
    `caller`, so HA's voice picker reflects the configured identity's
    permissions rather than every voice on the server."""
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
        for v in svc.backend.builtin_voices()
    ]
    voices.extend(
        TtsVoice(
            name=voice.qualified,
            description=f"clone: {voice.qualified}",
            attribution=attribution,
            installed=True,
            languages=["en"],
            version=None,
        )
        for voice in svc.voices.visible_to(caller)
    )

    return Info(
        tts=[
            TtsProgram(
                name="mimic-tts",
                description=f"mimic-tts ({svc.settings.backend})",
                attribution=attribution,
                installed=True,
                voices=voices,
                version="1",
                supports_synthesize_streaming=True,
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


class _MimicHandler(AsyncEventHandler):
    """One handler per Wyoming client connection. Every connection runs as
    the single `caller` identity resolved once at server startup."""

    def __init__(
        self,
        *args: Any,
        svc: Services,
        caller: Caller,
        info: Info,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._svc = svc
        self._caller = caller
        self._info = info
        # Per-connection streaming-input state. Populated on SynthesizeStart,
        # mutated by SynthesizeChunk, cleared on SynthesizeStop.
        self._stream_voice: str | None = None
        self._stream_buffer: str = ""
        self._stream_audio_started: bool = False
        self._stream_sr: int | None = None
        self._stream_aborted: bool = False

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self._info.event())
            return True

        if Synthesize.is_type(event.type):
            await self._stream_synthesis(Synthesize.from_event(event))
            return True

        if SynthesizeStart.is_type(event.type):
            start = SynthesizeStart.from_event(event)
            self._stream_voice = start.voice.name if start.voice else None
            self._stream_buffer = ""
            self._stream_audio_started = False
            self._stream_sr = None
            self._stream_aborted = False
            return True

        if SynthesizeChunk.is_type(event.type):
            if self._stream_aborted:
                return True
            chunk = SynthesizeChunk.from_event(event)
            self._stream_buffer += chunk.text
            await self._flush_complete_sentences()
            return True

        if SynthesizeStop.is_type(event.type):
            if not self._stream_aborted:
                # Flush whatever's left, even if it has no terminal punctuation.
                tail = self._stream_buffer.strip()
                self._stream_buffer = ""
                if tail:
                    await self._synth_and_emit(tail)
            if self._stream_audio_started:
                await self.write_event(AudioStop().event())
            # HA's wyoming TTS reader exits its read loop on SynthesizeStopped,
            # not AudioStop — without this, the HA-side HTTP tts_proxy hangs
            # waiting for more events until the TCP connection eventually
            # closes. Emit it unconditionally so HA always sees end-of-session.
            await self.write_event(SynthesizeStopped().event())
            # Reset for the next session on this connection.
            self._stream_voice = None
            self._stream_buffer = ""
            self._stream_audio_started = False
            self._stream_sr = None
            self._stream_aborted = False
            return True

        # Unknown event types — keep connection alive.
        return True

    async def _flush_complete_sentences(self) -> None:
        """Pop any complete sentences from the front of the buffer and emit
        them. Leaves a trailing partial fragment in the buffer for the next
        chunk to finish."""
        while True:
            match = _SENTENCE_BOUNDARY.search(self._stream_buffer)
            if not match:
                return
            sentence = self._stream_buffer[: match.start()].strip()
            self._stream_buffer = self._stream_buffer[match.end() :]
            if not sentence:
                continue
            # Honor the same coalesce rule as the batch path: if the sentence
            # is shorter than _MIN_SENTENCE_CHARS, push it back onto the
            # buffer so it merges with the next one.
            if len(sentence) < _MIN_SENTENCE_CHARS:
                self._stream_buffer = f"{sentence} {self._stream_buffer}"
                # Bail out — the next boundary (if any) is now past the merged
                # fragment, but we need more input to be sure. Wait for the
                # next chunk / stop.
                return
            await self._synth_and_emit(sentence)
            if self._stream_aborted:
                return

    async def _synth_and_emit(self, sentence: str) -> None:
        """Synthesize one sentence and emit AudioStart (once) + AudioChunks.
        Sets _stream_aborted on failure so callers can short-circuit."""
        try:
            samples, this_sr = synthesize(
                self._svc,
                self._caller,
                endpoint="wyoming",
                text=sentence,
                voice_spec=self._stream_voice or "default",
            )
        except (QuotaExceeded, VoiceNotFound) as e:
            # A failed utterance, not a dead connection: HA should see one
            # bad TTS request, not a server crash or a hung session.
            logger.warning("wyoming synth failed: %s", e.message)
            if not self._stream_audio_started:
                await self.write_event(Error(text=e.message, code=e.code).event())
            self._stream_aborted = True
            return
        except Exception as e:
            logger.exception("wyoming synth crashed")
            if not self._stream_audio_started:
                await self.write_event(Error(text=str(e), code="synth-error").event())
            self._stream_aborted = True
            return

        pcm_bytes, this_sr = _samples_to_int16_bytes(samples, this_sr)
        if not self._stream_audio_started:
            self._stream_sr = this_sr
            await self.write_event(AudioStart(rate=this_sr, width=2, channels=1).event())
            self._stream_audio_started = True
        sr = self._stream_sr
        assert sr is not None
        bytes_per_chunk = _CHUNK_SAMPLES * 2  # int16 mono
        for offset in range(0, len(pcm_bytes), bytes_per_chunk):
            chunk = pcm_bytes[offset : offset + bytes_per_chunk]
            await self.write_event(AudioChunk(audio=chunk, rate=sr, width=2, channels=1).event())

    async def _stream_synthesis(self, synth: Synthesize) -> None:
        """Legacy one-shot path: full text in a single Synthesize event.
        Generates audio sentence-by-sentence so the first sentence reaches
        the wire while later sentences are still being generated."""
        # Reuse the streaming-state machinery so there's one code path for
        # emit/error handling. This is a synthetic Start/Stop bracket.
        self._stream_voice = synth.voice.name if synth.voice else None
        self._stream_buffer = ""
        self._stream_audio_started = False
        self._stream_sr = None
        self._stream_aborted = False

        sentences = _split_sentences(synth.text)
        for sentence in sentences:
            if self._stream_aborted:
                break
            await self._synth_and_emit(sentence)

        if self._stream_audio_started:
            await self.write_event(AudioStop().event())
        # See note in SynthesizeStop handler — HA's wyoming TTS waits for
        # SynthesizeStopped to mark end of session.
        await self.write_event(SynthesizeStopped().event())

        self._stream_voice = None
        self._stream_audio_started = False
        self._stream_sr = None
        self._stream_aborted = False


async def run_wyoming_server(svc: Services) -> None:
    """Start the Wyoming TCP server. Cancellable via task.cancel().

    The caller identity is resolved once here, at startup, not per request:
    Wyoming holds one long-lived TCP listener with no per-connection
    credentials to re-resolve, and the deployment model (`MIMIC_WYOMING_KEY`
    set once, changed rarely) doesn't call for it. The tradeoff is the same
    one every long-lived `Caller` snapshot makes: if that key's quota or
    role is edited mid-run, this snapshot goes stale until the server
    restarts. Wyoming's traffic (one household's HA instance) makes that an
    acceptable staleness window.
    """
    settings = svc.settings
    caller = resolve_wyoming_caller(svc)
    info = _build_info(svc, caller)
    uri = f"tcp://{settings.wyoming_host}:{settings.wyoming_port}"
    logger.info(
        "starting Wyoming server on %s as key %r (NO AUTH — keep on tailnet/LAN only)",
        uri,
        caller.label,
    )
    server = AsyncServer.from_uri(uri)
    handler_factory = partial(_MimicHandler, svc=svc, caller=caller, info=info)
    await server.run(handler_factory)
