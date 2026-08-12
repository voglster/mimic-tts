"""Unit tests for the Wyoming server's pure helpers.

We don't spin up the TCP server here — that's an integration concern. We do
verify the voice-catalogue construction, sample-to-PCM conversion, the
sentence-splitting logic, and (the load-bearing part) that every synthesis
inside Wyoming goes through the same quota-checked, usage-recorded
`synth.synthesize` choke point as the HTTP routes, running as a resolved
`Caller` identity.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from conftest import _services
from mimic_server.identity import Caller
from mimic_server.synth import synthesize
from mimic_server.wyoming_server import (
    _build_info,
    _MimicHandler,
    _samples_to_int16_bytes,
    _split_sentences,
    resolve_wyoming_caller,
)
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
    SynthesizeVoice,
)


@pytest.fixture
def fake_backend():
    b = MagicMock()
    b.builtin_voices.return_value = [{"name": "default", "language": "English"}]
    b.synth_builtin.return_value = (np.zeros(1024, dtype=np.float32), 22050)
    b.synth_clone.return_value = (np.zeros(1024, dtype=np.float32), 22050)
    b.loaded_keys.return_value = []

    async def _no_lifecycle():
        return None

    b.run_lifecycle = _no_lifecycle
    return b


def _svc(tmp_path, fake_backend, **kw):
    """Assemble Services for these tests, defaulting api_token so bootstrap
    always has a root key to seed."""
    kw.setdefault("api_token", "t")
    return _services(tmp_path, fake_backend, **kw)


def test_wyoming_caller_defaults_to_root(tmp_path, fake_backend):
    svc = _svc(tmp_path, fake_backend)
    assert resolve_wyoming_caller(svc).label == svc.root.label


def test_wyoming_caller_uses_the_configured_label(tmp_path, fake_backend):
    svc = _svc(tmp_path, fake_backend, wyoming_key="ha")
    svc.keys.create("ha")
    assert resolve_wyoming_caller(svc).label == "ha"


def test_unknown_wyoming_label_falls_back_to_root_with_a_warning(tmp_path, fake_backend, caplog):
    svc = _svc(tmp_path, fake_backend, wyoming_key="ghost")
    with caplog.at_level("WARNING"):
        caller = resolve_wyoming_caller(svc)
    assert caller.label == svc.root.label
    assert "ghost" in caplog.text


def test_wyoming_synthesis_is_attributed_to_its_key(tmp_path, fake_backend):
    svc = _svc(tmp_path, fake_backend, wyoming_key="ha")
    svc.keys.create("ha")
    caller = resolve_wyoming_caller(svc)
    synthesize(svc, caller, endpoint="wyoming", text="hello", voice_spec="default")
    assert svc.usage.chars_today(caller.id) == 5


def test_wyoming_resolves_and_synthesizes_a_migrated_voice(tmp_path, fake_backend):
    """Task 7 migrated every voice from the flat `reference/<name>/` layout
    to `reference/<owner>/<name>/`. Before this task, Wyoming still read the
    flat layout directly and could never find a voice post-migration — this
    is the exact regression the owner's Home Assistant hit."""
    legacy = tmp_path / "reference" / "alice"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"")
    (legacy / "text.txt").write_text("hi")

    svc = _svc(tmp_path, fake_backend)  # bootstrap() runs the migration
    caller = resolve_wyoming_caller(svc)

    samples, sr = synthesize(
        svc, caller, endpoint="wyoming", text="hello there", voice_spec="alice"
    )

    fake_backend.synth_clone.assert_called_once()
    assert fake_backend.synth_clone.call_args.kwargs["name"] == f"{caller.label}/alice"
    assert sr == 22050
    assert samples is fake_backend.synth_clone.return_value[0]


def test_build_info_includes_builtin_voices(tmp_path, fake_backend):
    svc = _svc(tmp_path, fake_backend)
    caller = resolve_wyoming_caller(svc)
    info = _build_info(svc, caller)
    assert len(info.tts) == 1
    voice_names = [v.name for v in info.tts[0].voices]
    assert "default" in voice_names


def test_build_info_includes_visible_clone_voices(tmp_path, fake_backend):
    svc = _svc(tmp_path, fake_backend)
    caller = resolve_wyoming_caller(svc)
    svc.voices.register(caller, "alice", b"", "hi")
    info = _build_info(svc, caller)
    voice_names = [v.name for v in info.tts[0].voices]
    assert "default" in voice_names
    assert f"{caller.label}/alice" in voice_names


def test_build_info_reflects_the_configured_identitys_permissions(tmp_path, fake_backend):
    """A voice owned by someone other than the configured Wyoming identity,
    and never shared with it, must not appear in the catalogue HA sees."""
    svc = _svc(tmp_path, fake_backend, wyoming_key="ha")
    svc.keys.create("ha")
    dave, _ = svc.keys.create("dave")

    svc.voices.register(Caller(dave), "secret", b"", "hi")

    caller = resolve_wyoming_caller(svc)
    info = _build_info(svc, caller)
    voice_names = [v.name for v in info.tts[0].voices]
    assert "dave/secret" not in voice_names


def test_samples_to_int16_bytes_returns_pcm():
    samples = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    pcm, sr = _samples_to_int16_bytes(samples, 22050)
    assert sr == 22050
    assert len(pcm) == 2000  # 1000 samples * 2 bytes (int16)
    # Sanity: bytes decode back to int16 within range
    assert max(abs(v) for v in np.frombuffer(pcm, dtype=np.int16)) <= 32767


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


def test_build_info_advertises_streaming_support(tmp_path, fake_backend):
    svc = _svc(tmp_path, fake_backend)
    caller = resolve_wyoming_caller(svc)
    info = _build_info(svc, caller)
    assert info.tts[0].supports_synthesize_streaming is True


class _RecordingHandler(_MimicHandler):
    """Bypass AsyncEventHandler.__init__ (which expects a stream) and
    capture write_event calls in-memory."""

    def __init__(self, svc, caller, info):
        self._svc = svc
        self._caller = caller
        self._info = info
        self._stream_voice = None
        self._stream_buffer = ""
        self._stream_audio_started = False
        self._stream_sr = None
        self._stream_aborted = False
        self.events = []

    async def write_event(self, event):  # type: ignore[override]
        self.events.append(event)


def _make_handler(tmp_path, fake_backend, **kw):
    svc = _svc(tmp_path, fake_backend, **kw)
    caller = resolve_wyoming_caller(svc)
    info = _build_info(svc, caller)
    return _RecordingHandler(svc, caller, info)


def _event_types(events):
    return [e.type for e in events]


@pytest.mark.asyncio
async def test_streaming_session_emits_audio_before_stop(tmp_path, fake_backend):
    handler = _make_handler(tmp_path, fake_backend)

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
    # End of session: AudioStop then SynthesizeStopped (HA's reader breaks on the latter).
    assert SynthesizeStopped.is_type(types_after_stop[-1])
    assert AudioStop.is_type(types_after_stop[-2])
    # AudioStart should fire exactly once across the whole session.
    assert sum(1 for t in types_after_stop if AudioStart.is_type(t)) == 1


@pytest.mark.asyncio
async def test_streaming_session_flushes_trailing_partial_on_stop(tmp_path, fake_backend):
    handler = _make_handler(tmp_path, fake_backend)
    await handler.handle_event(SynthesizeStart().event())
    # No terminal punctuation — buffer should hold this until Stop.
    await handler.handle_event(SynthesizeChunk(text="trailing fragment with no terminator").event())
    assert fake_backend.synth_builtin.call_count == 0
    await handler.handle_event(SynthesizeStop().event())
    # Synthesized once on stop.
    assert fake_backend.synth_builtin.call_count == 1
    assert SynthesizeStopped.is_type(handler.events[-1].type)
    assert AudioStop.is_type(handler.events[-2].type)


@pytest.mark.asyncio
async def test_streaming_session_handles_split_across_chunks(tmp_path, fake_backend):
    handler = _make_handler(tmp_path, fake_backend)
    await handler.handle_event(SynthesizeStart().event())
    # Sentence boundary lands across two chunks — the period arrives in
    # chunk 1, but the capitalized continuation arrives in chunk 2.
    await handler.handle_event(SynthesizeChunk(text="First sentence here.").event())
    assert fake_backend.synth_builtin.call_count == 0  # no boundary yet (no whitespace+Capital)
    await handler.handle_event(SynthesizeChunk(text=" Second sentence here.").event())
    # Boundary now visible — first sentence flushed.
    assert fake_backend.synth_builtin.call_count == 1
    await handler.handle_event(SynthesizeStop().event())
    # Second sentence flushed on Stop.
    assert fake_backend.synth_builtin.call_count == 2


@pytest.mark.asyncio
async def test_legacy_synthesize_still_works(tmp_path, fake_backend):
    handler = _make_handler(tmp_path, fake_backend)
    await handler.handle_event(
        Synthesize(
            text="One two three. Four five six seven.", voice=SynthesizeVoice(name="default")
        ).event()
    )
    types = _event_types(handler.events)
    assert AudioStart.is_type(types[0])
    # End of session: AudioStop then SynthesizeStopped.
    assert SynthesizeStopped.is_type(types[-1])
    assert AudioStop.is_type(types[-2])
    assert fake_backend.synth_builtin.call_count == 2


@pytest.mark.asyncio
async def test_unknown_voice_fails_the_utterance_without_crashing(tmp_path, fake_backend):
    handler = _make_handler(tmp_path, fake_backend)

    await handler.handle_event(Synthesize(text="hi", voice=SynthesizeVoice(name="nobody")).event())

    types = _event_types(handler.events)
    assert any(Error.is_type(t) for t in types)
    assert not any(AudioStart.is_type(t) for t in types)
    assert SynthesizeStopped.is_type(types[-1])


@pytest.mark.asyncio
async def test_quota_exceeded_fails_the_utterance_without_crashing(tmp_path, fake_backend):
    svc = _svc(tmp_path, fake_backend, wyoming_key="ha")
    svc.keys.create("ha", daily_char_quota=1)
    caller = resolve_wyoming_caller(svc)
    handler = _RecordingHandler(svc, caller, _build_info(svc, caller))

    await handler.handle_event(Synthesize(text="this text exceeds the tiny quota").event())

    types = _event_types(handler.events)
    assert any(Error.is_type(t) for t in types)
    assert not any(AudioStart.is_type(t) for t in types)
    assert SynthesizeStopped.is_type(types[-1])
