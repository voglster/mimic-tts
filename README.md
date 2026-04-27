# mimic-tts

Self-hosted **Qwen3-TTS** voice cloning + synthesis. Runs on your own GPU,
ships with a tiny Python client, never sends a recording off your machine.

- Server — Docker image with on-demand model loading + idle unload.
- Client — `pip install mimic-tts` for a `mimic` CLI and a Python
  library (sync **and** async).
- Voice cloning — record a 10-second sample, register it, synthesize.
- Optional bearer auth — single env var flips it on.

## Quick start (server, Docker)

```bash
docker run --gpus all \
  -p 8000:8000 \
  -v mimic-data:/data \
  ghcr.io/jvogel/mimic-tts:latest
```

Or with `docker-compose.yml` from this repo:

```bash
docker compose up
```

Requires NVIDIA GPU + nvidia-container-toolkit. First run downloads ~7GB of
Qwen3-TTS weights into the `/data` volume; subsequent runs are fast.

## Quick start (client)

```bash
pip install mimic-tts

mimic say "hello, this is a test" --out hello.wav
mimic record alice                       # interactive: record + register a clone
mimic clone say alice "now I sound like alice"
mimic voices                             # list built-in voices
mimic clones                             # list registered clones
```

The client reads `MIMIC_SERVER_URL` from the environment, or
`~/.config/mimic/config.toml`:

```toml
server_url = "http://nas.local:8000"
token = "optional-bearer-token"
default_voice = "Ryan"
```

### Python library

```python
# Sync
from mimic import Client
with Client() as c:
    c.tts_to_file("hello there", "out.wav", speaker="Ryan")

# Async
from mimic import AsyncClient
async with AsyncClient() as c:
    audio = await c.tts("hello there")
```

## Endpoints (server)

| Method | Path                | What it does                                |
|--------|---------------------|---------------------------------------------|
| `POST` | `/tts`              | Built-in voice TTS (drop-in for `edge-tts`) |
| `POST` | `/clone/register`   | Register a reference voice (file + transcript) |
| `POST` | `/clone/tts`        | Synthesize using a registered clone         |
| `POST` | `/clone/oneshot`    | Clone + synthesize in one call              |
| `GET`  | `/voices`           | List built-in voices                        |
| `GET`  | `/clone/voices`     | List registered clone voices                |
| `GET`  | `/health`           | Loaded models + registered voices (always open) |

Set `MIMIC_API_TOKEN=...` to require `Authorization: Bearer ...` on every
endpoint except `/health`. Off by default.

## Built-in voices

| Voice      | Language |
|------------|----------|
| Ryan, Aiden | English |
| Vivian, Serena, Uncle_Fu, Dylan, Eric | Chinese |
| Ono_Anna   | Japanese |
| Sohee      | Korean   |

## Privacy

Reference recordings are stored locally on the server in
`MIMIC_REFERENCE_DIR` (default: `./reference`, or `/data/reference` in
Docker). They are not transmitted anywhere. The `.gitignore` excludes them
by default — you would have to add them deliberately to commit them.

## Documentation

- [Server reference](docs/server.md) — env vars, endpoints, GPU notes
- [Client reference](docs/client.md) — CLI, library, recording tips
- [Self-hosting guide](docs/self-hosting.md) — docker-compose, reverse proxy, auth

## Acknowledgements

Built on top of [Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base)
by the Qwen Team. See `NOTICE` for full attribution.

## License

MIT. See `LICENSE`.
