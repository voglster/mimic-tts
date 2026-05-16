#!/usr/bin/env bash
# Bench mimic-tts Wyoming server latency over the streaming-input path
# (SynthesizeStart / SynthesizeChunk / SynthesizeStop). This is the path
# Home Assistant uses when the server advertises
# supports_synthesize_streaming=True.
#
# We send the text in two chunks separated by a small delay to simulate
# tokens streaming in from an LLM. The metric that matters: time from the
# FIRST SynthesizeChunk send to the first AudioChunk.
set -euo pipefail

HOST="${1:-llmbox}"
PORT="${2:-10200}"
VOICE="${3:-piper}"
RUNS="${4:-3}"
TEXT="${5:-Sure thing. Let me find that for you right now while we keep chatting along.}"

uv run --with wyoming python - "$HOST" "$PORT" "$VOICE" "$RUNS" "$TEXT" <<'PY'
import asyncio, sys, time, statistics
from wyoming.client import AsyncTcpClient
from wyoming.tts import SynthesizeStart, SynthesizeChunk, SynthesizeStop, SynthesizeVoice
from wyoming.audio import AudioStart, AudioChunk, AudioStop

host, port, voice, runs, text = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5]
# Split at first sentence boundary if present, else at midpoint.
mid = text.find(". ")
if mid != -1:
    chunk1, chunk2 = text[: mid + 2], text[mid + 2 :]
else:
    half = len(text) // 2
    chunk1, chunk2 = text[:half], text[half:]

print(f"host: {host}:{port}  voice: {voice}  runs: {runs}  text: {len(text)} chars")
print(f"chunk1={chunk1!r}\nchunk2={chunk2!r}\n")
print(f"{'run':>3}  {'send→start':>12}  {'send→chunk1':>12}  {'send→stop':>10}  {'chunks':>7}  {'pcm_kB':>7}")

results = []
for i in range(1, runs + 1):
    async def one():
        async with AsyncTcpClient(host, port) as c:
            t_send = time.monotonic()
            await c.write_event(SynthesizeStart(voice=SynthesizeVoice(name=voice)).event())
            await c.write_event(SynthesizeChunk(text=chunk1).event())
            # Simulate the second batch of tokens arriving shortly after.
            await asyncio.sleep(0.05)
            await c.write_event(SynthesizeChunk(text=chunk2).event())
            await c.write_event(SynthesizeStop().event())
            t_start = t_first_chunk = t_stop = None
            chunks = 0
            pcm_bytes = 0
            while True:
                ev = await c.read_event()
                if ev is None:
                    break
                now = time.monotonic()
                if AudioStart.is_type(ev.type) and t_start is None:
                    t_start = now
                elif AudioChunk.is_type(ev.type):
                    if t_first_chunk is None:
                        t_first_chunk = now
                    chunks += 1
                    pcm_bytes += len(AudioChunk.from_event(ev).audio)
                elif AudioStop.is_type(ev.type):
                    t_stop = now
                    break
            return (t_start - t_send, t_first_chunk - t_send, t_stop - t_send, chunks, pcm_bytes)
    r = asyncio.run(one())
    results.append(r)
    print(f"{i:>3}  {r[0]:>12.3f}  {r[1]:>12.3f}  {r[2]:>10.3f}  {r[3]:>7}  {r[4]/1024:>7.1f}")

def stats(name, vals, unit="s"):
    print(f"{name:>14}: min={min(vals):.3f}{unit}  med={statistics.median(vals):.3f}{unit}  max={max(vals):.3f}{unit}")

print()
stats("send→start", [r[0] for r in results])
stats("send→chunk1", [r[1] for r in results])
stats("send→stop", [r[2] for r in results])
PY
