"""Unit tests for the Wyoming server's pure helpers.

We don't spin up the TCP server here — that's an integration concern. We do
verify the voice-catalogue construction, sample-to-PCM conversion, and the
synth routing logic, which is where the bugs hide.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi import HTTPException
from mimic_server.config import Settings
from mimic_server.wyoming_server import (
    _build_info,
    _MimicHandler,
    _route_synth,
    _samples_to_int16_bytes,
    _split_sentences,
)
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeVoice,
)


@pytest.fixture
def backend():
    b = MagicMock()
    b.builtin_voices.return_value = [{"name": "default", "language": "English"}]
    b.synth_builtin.return_value = (np.zeros(1024, dtype=np.float32), 22050)
    b.synth_clone.return_value = (np.zeros(1024, dtype=np.float32), 22050)
    return b


def _settings(tmp_path):
    return Settings(reference_dir=tmp_path, api_token="t")  # noqa: S106


def test_build_info_includes_builtin_voices(tmp_path, backend):
    info = _build_info(backend, _settings(tmp_path))
    assert len(info.tts) == 1
    voice_names = [v.name for v in info.tts[0].voices]
    assert "default" in voice_names


def test_build_info_includes_clone_voices_from_disk(tmp_path, backend):
    (tmp_path / "alice").mkdir()
    (tmp_path / "alice" / "audio.wav").write_bytes(b"")
    (tmp_path / "alice" / "text.txt").write_text("hi")
    info = _build_info(backend, _settings(tmp_path))
    voice_names = [v.name for v in info.tts[0].voices]
    assert "default" in voice_names
    assert "alice" in voice_names


def test_samples_to_int16_bytes_returns_pcm():
    samples = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    pcm, sr = _samples_to_int16_bytes(samples, 22050)
    assert sr == 22050
    assert len(pcm) == 2000  # 1000 samples * 2 bytes (int16)
    # Sanity: bytes decode back to int16 within range
    assert max(abs(v) for v in np.frombuffer(pcm, dtype=np.int16)) <= 32767


def test_route_synth_routes_builtin_to_synth_builtin(tmp_path, backend):
    _route_synth(backend, _settings(tmp_path), "hi", "default")
    backend.synth_builtin.assert_called_once()
    backend.synth_clone.assert_not_called()


def test_route_synth_routes_clone_to_synth_clone(tmp_path, backend):
    (tmp_path / "alice").mkdir()
    (tmp_path / "alice" / "audio.wav").write_bytes(b"")
    (tmp_path / "alice" / "text.txt").write_text("hi")
    _route_synth(backend, _settings(tmp_path), "hello", "alice")
    backend.synth_clone.assert_called_once()
    backend.synth_builtin.assert_not_called()


def test_route_synth_unknown_voice_raises_400(tmp_path, backend):
    with pytest.raises(HTTPException) as exc:
        _route_synth(backend, _settings(tmp_path), "hi", "nobody")
    assert exc.value.status_code == 400


def test_route_synth_none_voice_defaults_to_builtin(tmp_path, backend):
    _route_synth(backend, _settings(tmp_path), "hi", None)
    backend.synth_builtin.assert_called_once()
    assert backend.synth_builtin.call_args.kwargs["speaker"] == "default"


def test_split_sentences_basic():
    assert _split_sentences("Hello there. How are you today?") == [
        "Hello there.",
        "How are you today?",
    ]


def test_split_sentences_handles_exclamations_and_questions():
    out = _split_sentences("That's wild! Really? I had no idea today.")
    # "That's wild!" >= 10 chars → its own chunk
    # "Really?" < 10 chars → coalesces with next
    assert out == ["That's wild!", "Really? I had no idea today."]


def test_split_sentences_coalesces_short_fragments():
    # "Hi." is too short, gets merged into next sentence.
    assert _split_sentences("Hi. Welcome back to the show, friend.") == [
        "Hi. Welcome back to the show, friend.",
    ]


def test_split_sentences_no_boundaries_returns_single():
    assert _split_sentences("just one fragment with no terminator") == [
        "just one fragment with no terminator",
    ]


def test_split_sentences_quoted_followups_known_limitation():
    # Known limitation: when the sentence-ending punctuation sits INSIDE a
    # closing quote (`."`), the boundary regex doesn't match because the
    # punctuation isn't directly followed by whitespace. The whole string
    # synthesizes as one chunk — no streaming benefit, but it's correct.
    # Acceptable for HA assistant text which rarely uses quoted dialogue.
    out = _split_sentences('She said "hello." "Welcome to the future."')
    assert len(out) == 1


def test_split_sentences_does_not_split_on_abbreviations():
    # "Mr." would be a false positive split — but it's < 10 chars so it
    # coalesces into the next chunk, effectively handling abbreviations
    # for free for short common ones.
    out = _split_sentences("Mr. Smith arrived early today everyone.")
    assert len(out) == 1


def test_split_sentences_empty_input():
    assert _split_sentences("") == []
    assert _split_sentences("   ") == []


def test_build_info_advertises_streaming_support(tmp_path, backend):
    info = _build_info(backend, _settings(tmp_path))
    assert info.tts[0].supports_synthesize_streaming is True


class _RecordingHandler(_MimicHandler):
    """Bypass AsyncEventHandler.__init__ (which expects a stream) and
    capture write_event calls in-memory."""

    def __init__(self, backend, settings, info):
        self._backend = backend
        self._settings = settings
        self._info = info
        self._stream_voice = None
        self._stream_buffer = ""
        self._stream_audio_started = False
        self._stream_sr = None
        self._stream_aborted = False
        self.events = []

    async def write_event(self, event):  # type: ignore[override]
        self.events.append(event)


def _make_handler(tmp_path, backend):
    settings = _settings(tmp_path)
    info = _build_info(backend, settings)
    return _RecordingHandler(backend, settings, info)


def _event_types(events):
    return [e.type for e in events]


@pytest.mark.asyncio
async def test_streaming_session_emits_audio_before_stop(tmp_path, backend):
    handler = _make_handler(tmp_path, backend)

    await handler.handle_event(SynthesizeStart(voice=SynthesizeVoice(name="default")).event())
    # Boundary regex needs ".\s+[A-Z]" all visible — sentence 1 only flushes
    # once enough of sentence 2 has arrived. Mirror the realistic HA flow:
    # tokens stream in, boundary becomes visible mid-stream.
    await handler.handle_event(SynthesizeChunk(text="Hello there, friend. ").event())
    await handler.handle_event(SynthesizeChunk(text="Goodbye for now my dear.").event())

    types_before_stop = _event_types(handler.events)
    assert AudioStart.is_type(types_before_stop[0])
    assert any(AudioChunk.is_type(t) for t in types_before_stop), (
        "expected AudioChunk events before SynthesizeStop"
    )
    assert not any(AudioStop.is_type(t) for t in types_before_stop)

    await handler.handle_event(SynthesizeStop().event())

    types_after_stop = _event_types(handler.events)
    assert AudioStop.is_type(types_after_stop[-1])
    # AudioStart should fire exactly once across the whole session.
    assert sum(1 for t in types_after_stop if AudioStart.is_type(t)) == 1


@pytest.mark.asyncio
async def test_streaming_session_flushes_trailing_partial_on_stop(tmp_path, backend):
    handler = _make_handler(tmp_path, backend)
    await handler.handle_event(SynthesizeStart().event())
    # No terminal punctuation — buffer should hold this until Stop.
    await handler.handle_event(SynthesizeChunk(text="trailing fragment with no terminator").event())
    assert backend.synth_builtin.call_count == 0
    await handler.handle_event(SynthesizeStop().event())
    # Synthesized once on stop.
    assert backend.synth_builtin.call_count == 1
    assert AudioStop.is_type(handler.events[-1].type)


@pytest.mark.asyncio
async def test_streaming_session_handles_split_across_chunks(tmp_path, backend):
    handler = _make_handler(tmp_path, backend)
    await handler.handle_event(SynthesizeStart().event())
    # Sentence boundary lands across two chunks — the period arrives in
    # chunk 1, but the capitalized continuation arrives in chunk 2.
    await handler.handle_event(SynthesizeChunk(text="First sentence here.").event())
    assert backend.synth_builtin.call_count == 0  # no boundary yet (no whitespace+Capital)
    await handler.handle_event(SynthesizeChunk(text=" Second sentence here.").event())
    # Boundary now visible — first sentence flushed.
    assert backend.synth_builtin.call_count == 1
    await handler.handle_event(SynthesizeStop().event())
    # Second sentence flushed on Stop.
    assert backend.synth_builtin.call_count == 2


@pytest.mark.asyncio
async def test_legacy_synthesize_still_works(tmp_path, backend):
    handler = _make_handler(tmp_path, backend)
    await handler.handle_event(
        Synthesize(
            text="One two three. Four five six seven.", voice=SynthesizeVoice(name="default")
        ).event()
    )
    types = _event_types(handler.events)
    assert AudioStart.is_type(types[0])
    assert AudioStop.is_type(types[-1])
    assert backend.synth_builtin.call_count == 2
