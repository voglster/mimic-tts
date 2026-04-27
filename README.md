# qwen3-tts-api

A small FastAPI server wrapping [Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) for voice cloning and synthesis. Drop-in replacement for `edge-tts` plus voice-cloning endpoints.

Models load on demand and unload after 15s idle so the GPU can be shared with other workloads (e.g. an Ollama instance).

## Requirements

- Python 3.12
- CUDA GPU (tested on RTX 4090, ~6GB VRAM per loaded model)
- [`uv`](https://docs.astral.sh/uv/)

## Quick start (Docker)

```bash
docker compose up --build
curl -X POST http://localhost:8000/tts -F 'text=Hello there.' --output out.wav
```

> Requires NVIDIA GPU + nvidia-container-toolkit. The full README is being rewritten as part of the mimic-tts rebrand.

## Install & run

```bash
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

## Endpoints

### `POST /tts` — built-in voices (edge-tts compatible)
Form fields: `text`, `language` (default `English`), `speaker` (default `Ryan`), `instruct` (optional style prompt).
Returns: `audio/wav`.

```bash
curl -X POST http://localhost:8000/tts \
  -F 'text=Hello there, friend.' \
  -F 'speaker=Ryan' \
  --output out.wav
```

### `POST /clone/register` — register a reference voice
Form: `ref_audio` (file, ~3+s wav), `ref_text` (the transcript), `name` (default `default`).
The reference is persisted under `reference/<name>/` and loaded again on subsequent calls.

### `POST /clone/tts` — synthesize using a registered voice
Form: `text`, `language`, `name`.

### `POST /clone/oneshot` — clone + synthesize in one request
Form: `text`, `language`, `ref_audio`, `ref_text`. Slower per call; convenient for ad-hoc use.

### `GET /voices`
List built-in voices.

### `GET /clone/voices`
List registered clone voices.

### `GET /health`
Returns currently loaded models and registered voices.

## Built-in voices

`Ryan`, `Aiden` (English) · `Vivian`, `Serena`, `Uncle_Fu`, `Dylan`, `Eric` (Chinese) · `Ono_Anna` (Japanese) · `Sohee` (Korean).

## Notes

- The first call after idle takes ~10s to load the model. Subsequent calls are fast.
- Models unload automatically after 15 seconds of inactivity (`UNLOAD_AFTER` in `main.py`).
- Reference audio in `reference/` is gitignored — bring your own.

## License

MIT
