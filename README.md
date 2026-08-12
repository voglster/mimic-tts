# mimic-tts

Self-hosted **Chatterbox** voice cloning + synthesis. Runs on your own GPU,
ships with a tiny Python client, never sends a recording off your machine.

- Server — Docker image with on-demand model loading + idle unload.
- Client — `pip install mimic-tts` for a `mimic` CLI and a Python
  library (sync **and** async).
- Voice cloning — zero-shot from a ~10-second reference clip.
- Optional bearer auth — single env var flips it on.
- Multi-user key management — mint/revoke per-user API keys, each with its
  own voice ownership, visibility (private/public), and per-voice grants.
- Backend abstraction (`MIMIC_BACKEND`) — engine is swappable behind a
  small protocol; today ships Chatterbox, easy to add others.
- OpenAI-compatible `/v1/audio/speech` for HA `sfortis/openai_tts`,
  open-webui, etc.
- Optional Wyoming protocol server (HA-native voice pipeline) on a
  separate TCP port — same process, shared model in VRAM.

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

# multi-user (once MIMIC_API_TOKEN is set on the server)
mimic whoami                             # your key, role, quota, usage today
mimic share alice --to dave              # let another key use your voice
mimic admin key create dave --quota 100000  # mint a key for a friend (admin only)
mimic admin usage --key dave             # check what a key has used (admin only)
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
| `POST` | `/v1/audio/speech`  | OpenAI-compatible TTS (drop-in for HA `sfortis/openai_tts`) |
| `GET`  | `/voices`           | List built-in voices                        |
| `GET`  | `/clone/voices`     | List clone voices visible to the caller     |
| `GET`  | `/me`               | The caller's own identity, quota, and usage |
| `GET`, `POST`, `PATCH`, `DELETE` | `/admin/*` | Key management, server-wide usage and voices (admin only) |
| `GET`  | `/health`           | Bare liveness check — status/backend/stt_enabled only, always open |

Set `MIMIC_API_TOKEN=...` to require `Authorization: Bearer ...` on every
endpoint except `/health`; it becomes the admin key. See
[Multi-user access](docs/server.md#multi-user-access) for key lifecycle,
visibility, and grants.

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
