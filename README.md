# mimic-tts

Self-hosted **Chatterbox** voice cloning + synthesis. Runs on your own GPU,
ships with a tiny Python client, never sends a recording off your machine.

- Server — Docker image with on-demand model loading + idle unload.
- Client — `pip install mimic-tts` for a `mimic` CLI and a Python
  library (sync **and** async).
- Voice cloning — zero-shot from a ~10-second reference clip.
- Optional bearer auth — single env var flips it on.
- Backend abstraction (`MIMIC_BACKEND`) — engine is swappable behind a
  small protocol; today ships Chatterbox, easy to add others.

## Quick start (server, Docker)

```bash
docker run --gpus all \
  -p 8000:8000 \
  -v mimic-data:/data \
  ghcr.io/voglster/mimic-tts:latest
```

Or with `docker-compose.yml` from this repo:

```bash
docker compose up
```

Requires NVIDIA GPU + nvidia-container-toolkit. First run downloads
Chatterbox weights into the `/data` volume; subsequent runs are fast.

## Quick start (client)

```bash
pip install mimic-tts

mimic say "hello, this is a test" --out hello.wav  # default voice
mimic record alice --audio sample.wav --text "..."  # register a clone
mimic clone say alice "now I sound like alice"
mimic clones                             # list registered clones
mimic voices                             # list built-in voices (just "default")
```

The client reads `MIMIC_SERVER_URL` from the environment, or
`~/.config/mimic/config.toml`:

```toml
server_url = "http://nas.local:8000"
token = "optional-bearer-token"
default_voice = "default"   # or any registered clone name
```

### Python library

```python
# Sync
from mimic import Client
with Client() as c:
    c.tts_to_file("hello there", "out.wav")     # default voice
    c.clone_tts_to_file("alice", "hello there", "alice.wav")

# Async
from mimic import AsyncClient
async with AsyncClient() as c:
    audio = await c.tts("hello there")
```

## Endpoints (server)

| Method | Path                | What it does                                |
|--------|---------------------|---------------------------------------------|
| `POST` | `/tts`              | Default-voice TTS (no reference)            |
| `POST` | `/clone/register`   | Register a reference voice (file + transcript) |
| `POST` | `/clone/tts`        | Synthesize using a registered clone         |
| `POST` | `/clone/oneshot`    | Clone + synthesize in one call              |
| `GET`  | `/voices`           | List built-in voices                        |
| `GET`  | `/clone/voices`     | List registered clone voices                |
| `GET`  | `/health`           | Loaded models + registered voices (always open) |

Set `MIMIC_API_TOKEN=...` to require `Authorization: Bearer ...` on every
endpoint except `/health`. Off by default.

## Built-in voices

Chatterbox does not ship named celebrity voices. The server exposes one
built-in named `default` that uses Chatterbox's stock voice (no reference
audio). For any other voice, register a clone.

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

Built on top of [Chatterbox](https://github.com/resemble-ai/chatterbox) by
Resemble AI. See `NOTICE` for full attribution.

## License

MIT. See `LICENSE`.
