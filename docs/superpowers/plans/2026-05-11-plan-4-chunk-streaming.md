# Plan 4 — Real Chunk-Level Streaming TTS

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get **sub-500ms time-to-first-audio** on the Wyoming protocol path by replacing the current batched `chatterbox-tts` with the streaming-capable `chatterbox-streaming` fork. Today's sentence-level streaming gets us ~1.3s TTFA (down from ~2.4s batched). Real chunk streaming should drop us to ~500ms — the threshold where HA voice feels conversational instead of laggy.

**Architecture:** Extend the `TTSBackend` protocol with an async-generator method (`synth_clone_stream` / `synth_builtin_stream`) that yields `(samples, sample_rate)` tuples as chunks arrive from the model. The `ChatterboxBackend` wraps `model.generate_stream(...)`. The existing batch methods stay (they become thin wrappers that concat the stream) so the HTTP endpoints and tests don't need to change. The Wyoming handler ditches sentence-splitting and consumes the async generator directly — TTFA collapses to first-model-chunk time.

**Tech Stack:** Python 3.12, `chatterbox-streaming==0.1.2+` (replaces `chatterbox-tts`), torch 2.6 / cu124 (already in place), wyoming 1.8+, FastAPI.

**Source of truth:** This plan + benchmarks from `scripts/bench-wyoming.sh` against the live server.

**Out of scope (this plan):**
- HA-side integration wiring (server-side only)
- `torch.compile` warmup pass (separate optimization, ~1.5× modest synth speedup)
- Chunked HTTP transfer-encoding on `/v1/audio/speech` (low value — `sfortis/openai_tts` and most OpenAI-compat clients buffer the full response anyway)
- Wyoming **input** streaming (`SynthesizeChunk` events for incremental text from HA) — HA hands us full sentences, this is rarely useful

---

## Critical pre-flight: package swap, not addition

`chatterbox-tts` (current) and `chatterbox-streaming` (target) **cannot coexist** in one Python environment:

| Package | `transformers` pin |
|---|---|
| `chatterbox-tts` (current) | `==5.2.0` |
| `chatterbox-streaming` (davidbrowne17 fork) | `==4.46.3` |

Same dependency conflict that bit us when we tried to keep Qwen alongside Chatterbox in one image. The fix is identical: full swap, not optional extra. The backend abstraction we built in the Qwen → Chatterbox migration already isolates HTTP code from the engine, so the swap is contained to `server/mimic_server/backends/chatterbox.py` and `pyproject.toml`.

Risk to validate in **Task 1**: does the streaming fork actually produce audio of similar quality to the upstream Chatterbox? If quality regresses noticeably for our `piper` / `jim` clones, the plan needs to pivot (likely to a self-implemented streaming wrapper around upstream Chatterbox's internal `T3` model — much more work).

---

## Target metrics

Measured warm against `piper` on the llmbox 3080:

| | Batched (v0.1.4) | Sentence-streamed (v0.1.5, current) | Chunk-streamed (target) |
|---|---|---|---|
| TTFA, short opener (~12 chars) | 2.4s | 1.3s (median) | **< 0.5s** |
| TTFA, paragraph (~200 chars) | ~6s | ~2.3s | **< 0.5s** |
| Total wall-clock for 3 sentences | 2.4s* | 5.4s | 5.4s** |

\* Batched generates the whole thing as one synth — total time scales with text length but doesn't stream.
\** Total wall-clock barely changes; the win is perceptual. User hears audio while we keep generating.

Success criteria: median TTFA on `scripts/bench-wyoming.sh llmbox 10200 piper 5 "<short opener + 2 sentences>"` is **under 600ms** (warm, after the model has loaded into VRAM).

---

### Task 1: Spike — verify `chatterbox-streaming` works on llmbox

**Files:**
- No source changes. Local exploration only.

Throwaway investigation, in a scratch venv. Goal: confirm the fork loads, generates, and the streamed audio sounds equivalent to current Chatterbox output for our existing reference clips. Bail out early if quality is unacceptable — that determines whether the rest of this plan is viable.

- [ ] **Step 1: Create a scratch venv and install the streaming fork**

```bash
uv venv /tmp/cstream && source /tmp/cstream/bin/activate
uv pip install chatterbox-streaming==0.1.2
```

- [ ] **Step 2: Generate a clip from `piper`'s existing reference audio and listen**

Use the same reference file the live `piper` clone uses (`voices/voice_preview_piper.mp3` locally, or copy `audio.wav` off llmbox). Generate a multi-sentence response with `model.generate_stream(text, audio_prompt_path=...)`, concat the chunks, save WAV, compare against an equivalent batched output from the live server.

- [ ] **Step 3: Time the first chunk**

Wrap the iteration in `time.monotonic()` measurements. Confirm first chunk arrives well under 1s on the 3080 (the fork claims ~470ms on a 4090). If it's > 1s, real-streaming isn't actually a meaningful win over our sentence-streaming, and the plan needs reconsidering.

- [ ] **Step 4: Decision point**

Pass criteria: audible quality matches current Chatterbox and first-chunk latency is < 700ms warm. If both hold, proceed to Task 2. If quality is bad: stop, write up findings, consider self-implementing streaming around upstream Chatterbox's `T3` autoregressive loop (much larger effort — separate plan). If latency is bad: consider whether sentence-streaming is "good enough" and shelve this plan.

---

### Task 2: Swap the runtime dep

**Files:**
- Modify: `server/pyproject.toml`
- Modify: `uv.lock` (via `uv lock`)

- [ ] **Step 1: Replace `chatterbox-tts` with `chatterbox-streaming` in `server/pyproject.toml`**

```toml
dependencies = [
    "fastapi[standard]",
    "chatterbox-streaming>=0.1.2,<0.2",
    # perth (chatterbox's watermarker dep) imports `pkg_resources`, which was
    # removed as an importable module in setuptools 81. Pin <81 so it stays
    # available. Without it, model load silently sets PerthImplicitWatermarker
    # = None and crashes at first synth. NOTE: re-verify this is still needed
    # after the swap — chatterbox-streaming may bundle a different perth.
    "setuptools<81",
    # …rest unchanged…
]
```

- [ ] **Step 2: Re-lock**

```bash
uv lock
```

Watch for further conflicts. The streaming fork pins `transformers==4.46.3`, `diffusers==0.29.0`, `torch==2.6.0` — all should resolve. If the resolver complains about anything else, document it here and fix.

- [ ] **Step 3: Try a `uv sync --all-packages --all-extras` and import smoke test**

```bash
uv sync --all-packages --all-extras
uv run python -c "from chatterbox.tts import ChatterboxTTS; print('ok')"
```

Confirm the import path is still `chatterbox.tts` (the fork keeps the upstream namespace). If it diverges, Task 3 needs the new import path.

- [ ] **Step 4: Verify the pkg_resources fix still applies**

```bash
uv run python -c "import pkg_resources; import perth; print(perth.PerthImplicitWatermarker)"
```

Should print a non-None class. If `None`, the `setuptools<81` pin needs to stay or another fix is needed.

---

### Task 3: Extend `TTSBackend` protocol with async streaming

**Files:**
- Modify: `server/mimic_server/backends/base.py`

Add two streaming methods next to the existing batch ones. The Protocol stays backwards-compatible: backends that don't implement streaming raise `NotImplementedError`, and the Wyoming handler will fall back to sentence-batching in that case.

- [ ] **Step 1: Add methods to `TTSBackend` protocol**

```python
def synth_builtin_stream(
    self,
    *,
    text: str,
    speaker: str,
    language: str = "English",
    instruct: str | None = None,
) -> AsyncIterator[tuple[Any, int]]:
    """Stream variant of synth_builtin. Yields (samples, sample_rate)
    tuples as the model produces chunks. Sample rate is constant across
    all chunks from one call."""

def synth_clone_stream(
    self,
    *,
    name: str,
    text: str,
    ref_audio_path: Path,
    ref_text: str,
    language: str = "English",
) -> AsyncIterator[tuple[Any, int]]:
    """Stream variant of synth_clone."""

def supports_streaming(self) -> bool:
    """True if synth_*_stream emit real chunks (rather than one fat
    yield at the end). Wyoming handler uses this to decide whether to
    fall back to sentence-batching."""
```

- [ ] **Step 2: Imports**

```python
from collections.abc import AsyncIterator
```

- [ ] **Step 3: No tests changed yet — existing tests use `fake_backend = MagicMock()` which auto-implements anything.**

---

### Task 4: Implement streaming in `ChatterboxBackend`

**Files:**
- Modify: `server/mimic_server/backends/chatterbox.py`

Replace the `model.generate(text, audio_prompt_path=...)` call with `model.generate_stream(text, audio_prompt_path=..., chunk_size=N)`, exposed via the new async-generator methods. Keep the existing batch methods, but rewrite them as thin wrappers that consume the stream and concat (single source of truth for synthesis logic).

- [ ] **Step 1: Add a private `_stream_generate()` helper**

The fork's `generate_stream` is a synchronous generator yielding `(chunk_tensor, metrics)`. We need an async generator that yields to the event loop between chunks so we don't block uvicorn. Use `asyncio.to_thread` to pull each chunk on a worker, then yield it.

```python
async def _stream_generate(
    self, *, text: str, audio_prompt_path: str | None
) -> AsyncIterator[tuple[Any, int]]:
    model = self._mm.get(MODEL_KEY)
    gen = model.generate_stream(
        text=text,
        audio_prompt_path=audio_prompt_path,
        chunk_size=50,  # default from fork — 50 speech tokens per chunk
    )
    sr = int(model.sr)
    # generate_stream is a sync iterator from the underlying torch loop;
    # pull each chunk off the worker pool so we don't block uvicorn.
    sentinel = object()

    def _next():
        try:
            return next(gen)
        except StopIteration:
            return sentinel

    while True:
        item = await asyncio.to_thread(_next)
        if item is sentinel:
            return
        chunk_tensor, _metrics = item
        yield _to_numpy(chunk_tensor), sr
```

- [ ] **Step 2: Implement `synth_builtin_stream` and `synth_clone_stream`**

Both call `_stream_generate` with the appropriate `audio_prompt_path` (None for builtin "default", the ref path for clones). The existing `synth_builtin`/`synth_clone`/`synth_clone_oneshot` become consumers of these — they collect all chunks and `np.concatenate` them.

- [ ] **Step 3: `supports_streaming()` returns True**

- [ ] **Step 4: Update the cold-load test logging**

The existing log line `loaded Chatterbox in %.1fs` is fine. Add a one-time DEBUG log on the first stream call confirming we're using the streaming path, so we can tell from container logs that the new code is live.

---

### Task 5: Refactor Wyoming handler to consume the stream

**Files:**
- Modify: `server/mimic_server/wyoming_server.py`

Replace `_stream_synthesis` (sentence-splitter) with a direct consumer of the backend's async stream. Keep `_split_sentences` and the old loop as a fallback for backends where `supports_streaming()` is False.

- [ ] **Step 1: Add a streaming path**

```python
async def _stream_synthesis_chunks(self, synth: Synthesize) -> None:
    """Consume backend's chunk stream directly. TTFA = time to first
    model chunk."""
    voice_name = synth.voice.name if synth.voice else None
    # Decide builtin vs clone the same way _route_synth does, but call
    # the *_stream variant. Pull stream metadata + first chunk together
    # to establish AudioStart with the correct SR.

    audio_started = False
    sr: int | None = None
    bytes_per_chunk = _CHUNK_SAMPLES * 2

    try:
        async for samples, this_sr in self._route_synth_stream(synth.text, voice_name):
            pcm_bytes, this_sr = _samples_to_int16_bytes(samples, this_sr)
            if not audio_started:
                sr = this_sr
                await self.write_event(AudioStart(rate=sr, width=2, channels=1).event())
                audio_started = True
            for offset in range(0, len(pcm_bytes), bytes_per_chunk):
                chunk = pcm_bytes[offset : offset + bytes_per_chunk]
                await self.write_event(
                    AudioChunk(audio=chunk, rate=sr, width=2, channels=1).event()
                )
    except HTTPException as e:
        # only emit Error if nothing has started; otherwise close cleanly
        ...

    if audio_started:
        await self.write_event(AudioStop().event())
```

`_route_synth_stream(text, voice_name)` is the streaming sibling of `_route_synth`, dispatching to `synth_builtin_stream` / `synth_clone_stream`.

- [ ] **Step 2: Branch on `backend.supports_streaming()`**

```python
async def handle_event(self, event):
    if Synthesize.is_type(event.type):
        synth = Synthesize.from_event(event)
        if self._backend.supports_streaming():
            await self._stream_synthesis_chunks(synth)
        else:
            await self._stream_synthesis(synth)  # sentence-level fallback
        return True
```

- [ ] **Step 3: Keep `_split_sentences` and the sentence-level path**

Don't delete the sentence-level streaming code. It's the fallback for non-streaming backends (e.g. future Voxtral backend that doesn't have chunk support yet), and it's a working safety net if the chunk path breaks.

---

### Task 6: Tests

**Files:**
- Modify: `server/tests/test_wyoming.py`
- Modify: `server/tests/test_app.py` (if needed)

Existing tests use `MagicMock()` for the backend, which auto-implements both `synth_clone` (batch) and `synth_clone_stream` (async generator returning a single yield). That mostly Just Works — but the new branch in `handle_event` needs explicit coverage.

- [ ] **Step 1: Add `supports_streaming` to the fake_backend fixture**

Default the mock to `supports_streaming.return_value = False` so existing tests continue to exercise the sentence path. Add new tests that flip it to True with a streaming generator and verify chunks come out correctly.

- [ ] **Step 2: New test: streaming path emits multiple AudioChunks**

```python
async def test_streaming_backend_yields_progressive_chunks(...):
    backend = MagicMock()
    backend.supports_streaming.return_value = True
    async def fake_stream(**kw):
        yield np.zeros(1024, dtype=np.float32), 24000
        yield np.zeros(1024, dtype=np.float32), 24000
        yield np.zeros(1024, dtype=np.float32), 24000
    backend.synth_builtin_stream.side_effect = fake_stream
    # ...drive the Wyoming handler and assert N AudioChunk events received
```

- [ ] **Step 3: New test: streaming-disabled backends fall back to sentence path**

Same fixture but `supports_streaming.return_value = False`. Existing fixture would already use sentence-level streaming.

- [ ] **Step 4: Backend-level test**

Add `server/tests/test_chatterbox_backend.py` that mocks the underlying `ChatterboxTTS.from_pretrained` and `model.generate_stream`. Verify our async wrapper correctly forwards arguments and yields chunks. Don't need to hit a real GPU.

---

### Task 7: Bench + tune `chunk_size`

**Files:**
- No code changes (might add `MIMIC_CHATTERBOX_CHUNK_SIZE` env var if tuning matters)

The fork's default `chunk_size=50` (speech tokens) is a starting point. We want the smallest value that still produces clean audio. Smaller = lower TTFA, but tiny chunks can have artifacts.

- [ ] **Step 1: Run `bench-wyoming.sh` with default chunk size**

Target: TTFA < 600ms median on `"Sure thing. Let me find that for you right now while we keep chatting along."` (the existing baseline phrase).

- [ ] **Step 2: A/B chunk sizes 25, 50, 100**

Add a temporary env var (`MIMIC_CHATTERBOX_CHUNK_SIZE`) wired into the backend if not already done. Compare TTFA, total time, and listen for audio glitches at each setting. Pick the best.

- [ ] **Step 3: If meaningful tuning emerges, expose `MIMIC_CHATTERBOX_CHUNK_SIZE` in `config.py` + `docs/server.md`**

Otherwise hardcode the chosen default and skip the config knob.

---

### Task 8: Release + deploy + verify

**Files:**
- No source changes. Release wrapper + compose redeploy + production bench.

- [ ] **Step 1: `./release.sh minor -y`**

This is a meaningful enough change to warrant a minor bump (0.1.x → 0.2.0). Communicates "streaming behavior has changed" to anyone tracking versions.

- [ ] **Step 2: Wait for GHCR manifest, pull on llmbox, restart compose**

- [ ] **Step 3: Bench on the live server**

```bash
scripts/bench-wyoming.sh llmbox 10200 piper 5 "Sure thing. Let me find that for you right now while we keep chatting along."
```

Confirm TTFA median is under target. If not, revisit Task 7 chunk_size or Task 1's quality decision.

- [ ] **Step 4: Listen to actual output**

Run a `wyoming-client` synth call (the inline Python from earlier session), save the WAV, play it. Confirm:
- No artifacts at chunk boundaries
- Voice matches expected `piper` character
- No clipping or weird pacing

- [ ] **Step 5: Update `docs/server.md`**

Note that streaming is now real chunk-streaming when on the chatterbox-streaming backend. Update perf numbers.

- [ ] **Step 6: Update `~/.config/lumbergh/shared/mimic-tts-for-ha.md` if HA-relevant numbers changed**

(Optional — not strictly part of the source tree, but useful for the HA-Claude handoff.)

---

## Risks and known unknowns

- **Fork maintenance**: `davidbrowne17/chatterbox-streaming` is a community fork. It may lag upstream Chatterbox feature work (e.g. Chatterbox Turbo support). Lock to a tested version and don't auto-upgrade.
- **Audio quality regression**: if the streaming fork produces measurably worse audio than upstream, Task 1's go/no-go decision kicks in. Fallback plan: stay on `chatterbox-tts` + sentence streaming.
- **`asyncio.to_thread` per chunk overhead**: each chunk pull bounces through a thread. For 50-token chunks (~1s of audio each), this is negligible. For smaller chunks it could add up — Task 7 should rule this out.
- **HA buffering**: even with sub-500ms TTFA from us, HA's voice pipeline has its own buffering (resampling, format conversion, playback buffer). End-to-end perceived latency might still feel ~700-900ms. That's outside our control but worth measuring with an actual HA setup.
- **The pkg_resources / setuptools dance** may need to be re-derived after the swap. The fork pulls a different `perth` version potentially. Task 2 Step 4 catches it.

## After this plan

- HA voice should feel conversational. If it still doesn't, look at HA-side audio pipeline buffering, not us.
- The other queued items (`torch.compile` warmup, HA wiring) are independent — pick them up separately.
