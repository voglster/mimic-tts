# Server reference

The mimic-tts server is a thin FastAPI wrapper around Qwen3-TTS, packaged
as a Docker image (`ghcr.io/<owner>/mimic-tts`) and as a `mimic-server`
console entry on PyPI's `mimic-server` workspace member (server is not
distributed via PyPI — install it via Docker or from source).

## Configuration (env vars)

| Variable | Default (local) | Default (Docker, with `MIMIC_DATA_DIR=/data`) | Purpose |
|---|---|---|---|
| `MIMIC_HOST` | `127.0.0.1` | `0.0.0.0` | Bind host |
| `MIMIC_PORT` | `8000` | `8000` | Bind port |
| `MIMIC_REFERENCE_DIR` | `./reference` | `/data/reference` | Persisted clone reference audio + transcripts |
| `MIMIC_MODEL_CACHE` | (HF default) | `/data/models` | Sets `HF_HOME`; weights cache here |
| `MIMIC_UNLOAD_AFTER` | `0` | `0` | Seconds idle before models unload (`0` = keep loaded forever) |
| `MIMIC_API_TOKEN` | unset | unset | Optional bearer token (off by default) |
| `MIMIC_LOG_LEVEL` | `INFO` | `INFO` | Log level |
| `MIMIC_BACKEND` | `chatterbox` | `chatterbox` | TTS engine (currently only `chatterbox`) |
| `MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND` | `false` | `false` | Allow public bind without `MIMIC_API_TOKEN` (set when a reverse proxy / tailnet ACL handles auth upstream) |
| `MIMIC_WYOMING_ENABLED` | `false` | `false` | Start the Wyoming TCP server alongside FastAPI (shared model in VRAM) |
| `MIMIC_WYOMING_HOST` | `0.0.0.0` | `0.0.0.0` | Wyoming bind interface (inside the container — host firewall is the actual boundary) |
| `MIMIC_WYOMING_PORT` | `10200` | `10200` | Wyoming TCP port |

## Endpoints

All endpoints accept and return form-encoded data unless noted. Audio
responses are `audio/wav`.

### `POST /tts` — built-in voices
Form fields: `text` (required), `language` (default `English`), `speaker`
(default `default`), `instruct` (ignored by Chatterbox).

Chatterbox ships one built-in voice named `default`. For any other voice,
register a clone.

### `POST /clone/register` — register a clone
Form fields: `name` (default `default`), `ref_text` (the transcript),
`ref_audio` (file, ~3+ seconds wav).

The reference is persisted to `MIMIC_REFERENCE_DIR/<name>/audio.wav` +
`text.txt`. Subsequent calls to `/clone/tts` reload it from disk if the
in-memory prompt was unloaded.

### `POST /clone/tts` — synthesize using a registered clone
Form fields: `text`, `language`, `name`.

### `POST /clone/oneshot` — clone + synthesize in one call
Form fields: `text`, `language`, `ref_audio` (file), `ref_text`.
Slower than register-then-call, but doesn't persist anything.

### `GET /voices`, `GET /clone/voices`, `GET /health`
JSON lists. `/health` is always unauthenticated.

### `POST /v1/audio/speech` — OpenAI-compatible
JSON body matching OpenAI's TTS API:

```json
{
  "model": "tts-1",           // ignored, single engine
  "input": "text to speak",
  "voice": "default",         // built-in name OR registered clone name
  "response_format": "wav",   // wav | flac | pcm  (mp3/opus/aac require an encoder we don't ship)
  "speed": 1.0                // ignored (Chatterbox has no native speed knob)
}
```

Returns raw audio bytes with the appropriate Content-Type. Designed to be a
drop-in for the [`sfortis/openai_tts`](https://github.com/sfortis/openai_tts)
Home Assistant integration and any other tool that speaks OpenAI's TTS API
(open-webui, LibreChat, etc.).

## GPU + memory

Chatterbox loads on `cuda` (auto-falls back to CPU). Takes a few GB VRAM.

By default the model stays loaded once warm (`MIMIC_UNLOAD_AFTER=0`) — best
for low-latency interactive use like Home Assistant voice. Set it to a
positive number of seconds if you'd rather free VRAM after idle (useful
when sharing the GPU with other workloads like a local Ollama). First call
takes ~10s for the model to load; subsequent calls are fast.

## Auth

`MIMIC_API_TOKEN=secret` flips on bearer auth for every endpoint except
`/health`. The check uses `secrets.compare_digest` (constant-time).

**Public-bind safety check**: if `MIMIC_HOST` is non-loopback (e.g. `0.0.0.0`)
and `MIMIC_API_TOKEN` is unset, the server refuses to start. This prevents
the "oops I exposed it" scenario. If a reverse proxy / tailnet ACL is
enforcing auth upstream and you really do want no app-level token, set
`MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND=1` explicitly.

## Wyoming protocol (Home Assistant voice pipeline)

Opt-in via `MIMIC_WYOMING_ENABLED=true`. When enabled, a Wyoming TCP server
runs in the same process as FastAPI — both share the loaded model in VRAM,
no duplication.

**No auth**: the Wyoming protocol does not support auth, TLS, or any
handshake. The trust boundary is the network. The container binds to
`0.0.0.0:10200` by default; protect it by:

- Mapping the host port only to tailnet / LAN interfaces, or
- Not adding a public reverse-proxy entry for port 10200 (your existing
  HTTP reverse proxy won't accidentally pick this up — Wyoming is TCP, not
  HTTP).

Add to your `docker-compose.yml`:

```yaml
services:
  mimic-tts:
    ports:
      - "8000:8000"
      - "10200:10200"   # Wyoming — keep off the public internet
    environment:
      MIMIC_WYOMING_ENABLED: "true"
```

Then in Home Assistant, add the Wyoming integration pointing at
`tcp://<llmbox-host>:10200`. HA will discover the registered voices
(built-ins + clones) via the `Describe` event.

There's intentionally no token rotation, no per-user tokens, no JWT, and no
TLS termination — that's your reverse proxy's job. See
[self-hosting](self-hosting.md).
