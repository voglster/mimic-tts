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
| `MIMIC_UNLOAD_AFTER` | `15` | `15` | Seconds idle before models unload |
| `MIMIC_API_TOKEN` | unset | unset | Optional bearer token (off by default) |
| `MIMIC_LOG_LEVEL` | `INFO` | `INFO` | Log level |

## Endpoints

All endpoints accept and return form-encoded data unless noted. Audio
responses are `audio/wav`.

### `POST /tts` — built-in voices
Form fields: `text` (required), `language` (default `English`), `speaker`
(default `Ryan`), `instruct` (optional style cue).

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

## GPU + memory

Qwen3-TTS loads in `bfloat16` on `cuda:0`. Each model takes ~6GB VRAM. The
server unloads any unused model after `MIMIC_UNLOAD_AFTER` seconds idle so
you can share the GPU with other workloads (e.g. a local Ollama).

First call after idle takes ~10s for the model to load; subsequent calls
are fast.

## Auth

`MIMIC_API_TOKEN=secret` flips on bearer auth for every endpoint except
`/health`. The check uses `secrets.compare_digest` (constant-time).

There's intentionally no token rotation, no per-user tokens, no JWT, and no
TLS termination — that's your reverse proxy's job. See
[self-hosting](self-hosting.md).
