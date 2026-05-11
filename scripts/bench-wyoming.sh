#!/usr/bin/env bash
# Bench mimic-tts Wyoming server latency, with emphasis on time-to-first-audio.
#
# For conversational use the metric that matters is how long after Synthesize
# is sent before the FIRST AudioChunk arrives — that's when HA can start
# playing back. With non-streaming generation, AudioStart and the first chunk
# arrive nearly together at the END of generation.
set -euo pipefail

HOST="${1:-llmbox}"
PORT="${2:-10200}"
VOICE="${3:-piper}"
RUNS="${4:-5}"
TEXT="${5:-Speaking through Wyoming protocol. Same model, different door.}"

uv run --with wyoming python - "$HOST" "$PORT" "$VOICE" "$RUNS" "$TEXT" <<'PY'
import asyncio, sys, time, statistics
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice
from wyoming.audio import AudioStart, AudioChunk, AudioStop

host, port, voice, runs, text = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5]
print(f"host: {host}:{port}  voice: {voice}  runs: {runs}  text: {len(text)} chars\n")
print(f"{'run':>3}  {'send→start':>12}  {'send→chunk1':>12}  {'send→stop':>10}  {'chunks':>7}  {'pcm_kB':>7}")

first_start = first_chunk = total = []
results = []
for i in range(1, runs + 1):
    async def one():
        async with AsyncTcpClient(host, port) as c:
            t_send = time.monotonic()
            await c.write_event(Synthesize(text=text, voice=SynthesizeVoice(name=voice)).event())
            t_start = t_first_chunk = None
            chunks = 0
            pcm_bytes = 0
            while True:
                ev = await c.read_event()
                if ev is None: break
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