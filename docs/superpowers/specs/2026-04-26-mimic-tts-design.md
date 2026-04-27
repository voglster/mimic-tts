# mimic-tts — Design Spec

**Date:** 2026-04-26
**Status:** Approved (brainstorming) — pending implementation plan

## Goal

Rebrand the existing `qwen3-tts-api` project to **mimic-tts** and prepare it for a public GitHub release as a two-part product:

1. **`mimic-server`** — a self-hosted FastAPI server wrapping Qwen3-TTS for synthesis and voice cloning, distributed as a Docker image on GHCR.
2. **`mimic-tts`** — a `pip install`-able Python client (library + `mimic` CLI) that talks to the server, including a guided microphone-recording flow for voice cloning.

The repo must be safe to publish (no secrets, no personal voice samples), with sensible documentation, CI, and a release pipeline modeled after the `lumbergh` project's pattern.

## Naming

- **Project / repo / Docker image:** `mimic-tts`
- **PyPI package (client):** `mimic-tts` → `pip install mimic-tts` → `import mimic`
- **CLI entry point:** `mimic`
- **Docker image:** `ghcr.io/<owner>/mimic-tts`

## Repo layout (monorepo, uv workspace)

```
mimic-tts/
├── server/
│   ├── pyproject.toml            # name = "mimic-server" (NOT published to PyPI)
│   ├── mimic_server/
│   │   ├── __init__.py
│   │   ├── app.py                # FastAPI app (refactored from current main.py)
│   │   ├── config.py             # env-var driven settings
│   │   ├── auth.py               # optional bearer-token middleware
│   │   └── models.py             # model load/unload manager
│   └── Dockerfile
├── client/
│   ├── pyproject.toml            # name = "mimic-tts" (PyPI)
│   ├── mimic/
│   │   ├── __init__.py           # exports Client, AsyncClient
│   │   ├── _base.py              # shared request-building/auth/error logic
│   │   ├── client.py             # sync Client (httpx.Client)
│   │   ├── async_client.py       # AsyncClient (httpx.AsyncClient)
│   │   ├── cli.py                # typer-based CLI; entry point `mimic`
│   │   ├── recorder.py           # sounddevice recording flow
│   │   └── config.py             # env + ~/.config/mimic/config.toml
│   └── README.md
├── .github/workflows/
│   ├── ci.yml                    # lint + tests on PR/main
│   └── release.yml               # on tag v*: PyPI + GHCR + GH release
├── docs/
│   ├── server.md
│   ├── client.md
│   └── self-hosting.md
├── release.sh                    # adapted from lumbergh
├── lint.sh
├── docker-compose.yml            # example one-command server run
├── README.md                     # project landing — quickstart for both
├── LICENSE                       # MIT
├── NOTICE                        # Qwen3-TTS upstream attribution
├── CONTRIBUTING.md
└── pyproject.toml                # uv workspace root
```

The `uv` workspace ties server + client together for local dev; only `client/` ships to PyPI. Both `pyproject.toml` files share a single version, rewritten by `release.sh` at release time.

## Server

### Refactor

Move `main.py` → `mimic_server/app.py`; split into modules:

- `config.py` — pydantic-settings or simple `os.environ` reader producing a `Settings` dataclass.
- `auth.py` — FastAPI dependency that, when `MIMIC_API_TOKEN` is set, requires `Authorization: Bearer <token>` on all routes except `/health`. When unset, all routes are open.
- `models.py` — the existing `_load_model` / `_unload_all` / `get_model` / `_unload_watcher` logic, encapsulated in a `ModelManager` class.
- `app.py` — FastAPI app, lifespan, and route handlers only.

### Configuration (env vars)

| Variable | Default (local) | Default (Docker) | Purpose |
|---|---|---|---|
| `MIMIC_HOST` | `127.0.0.1` | `0.0.0.0` | bind host |
| `MIMIC_PORT` | `8000` | `8000` | bind port |
| `MIMIC_REFERENCE_DIR` | `./reference` | `/data/reference` | persisted clone reference audio + transcripts |
| `MIMIC_MODEL_CACHE` | (uses HF default) | `/data/models` | sets `HF_HOME` so weights cache to a mounted volume |
| `MIMIC_UNLOAD_AFTER` | `15` | `15` | seconds idle before models unload |
| `MIMIC_API_TOKEN` | unset | unset | optional bearer token; off by default |
| `MIMIC_LOG_LEVEL` | `INFO` | `INFO` | log level |

Docker defaults are selected when `MIMIC_DATA_DIR=/data` is set (the Dockerfile sets this).

### Console entry point

`mimic-server` (declared in `server/pyproject.toml`) runs uvicorn with the configured host/port. Used both by the Docker `CMD` and locally via `uv run mimic-server`.

### Endpoints

Endpoint shape unchanged from the current implementation:

- `POST /tts` — built-in voices
- `POST /clone/register` — register a reference voice
- `POST /clone/tts` — synthesize using a registered voice
- `POST /clone/oneshot` — clone + synthesize in one call
- `GET /voices` — list built-in voices
- `GET /clone/voices` — list registered clone voices
- `GET /health` — model + voice state (always unauthenticated)

## Client (`mimic-tts` on PyPI, CLI: `mimic`)

### Library

Both sync and async clients share an internal `_BaseClient` for request construction, auth header injection, and error mapping. Public surface:

```python
from mimic import Client, AsyncClient

# Sync
c = Client(server_url="http://localhost:8000", token=None)
c.tts("hello", speaker="Ryan")                          # -> bytes (wav)
c.tts_to_file("hello", "out.wav", speaker="Ryan")
c.clone_register("alice", "ref.wav", "transcript")
c.clone_tts("alice", "hello in alice's voice")
c.clone_oneshot(text, "ref.wav", "transcript")
c.list_voices(); c.list_clones(); c.health()

# Async — same surface, awaitable
async with AsyncClient() as c:
    await c.tts("hello")
    await c.clone_register("alice", "ref.wav", "transcript")
```

Both clients fall back to env vars for `server_url` and `token` if not passed explicitly.

### CLI (typer)

```
mimic say "hello"  [--voice Ryan] [--out file.wav] [--play]
mimic record <name>                             # guided recorder (default)
mimic record <name> --audio f.wav --text "…"    # skip recording
mimic clone say <name> "hello"
mimic voices                                    # list built-in voices
mimic clones                                    # list registered clones
mimic config                                    # show effective config
mimic health
```

### Recording flow (`mimic record <name>`)

1. Print a 4-sentence script chosen for varied phonemes.
2. Prompt: "Press Enter to start recording, Ctrl+C to abort."
3. Countdown 3-2-1, then record via `sounddevice` until Enter is pressed (cap 30s).
4. Play back the recording.
5. Prompt: "Keep this take? [y/N/r=retry]" — `r` loops back to step 2.
6. On keep: confirm transcript (default = the printed script), POST to `/clone/register`.

### Config resolution

flag > env (`MIMIC_SERVER_URL`, `MIMIC_API_TOKEN`) > `~/.config/mimic/config.toml` > defaults.

`~/.config/mimic/config.toml` example:
```toml
server_url = "http://nas.local:8000"
token = "…"
default_voice = "Ryan"
```

### Python range

`>=3.12` — same as the server, keep users current.

### Dependencies

`httpx`, `typer`, `sounddevice`, `soundfile`, `numpy`, `platformdirs`.

## Dockerfile

`server/Dockerfile` (CUDA 12.1 runtime base, weights NOT baked in):

```dockerfile
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04
RUN apt-get update && apt-get install -y python3.12 python3-pip libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
WORKDIR /app
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev
COPY server/mimic_server ./mimic_server
ENV MIMIC_DATA_DIR=/data HF_HOME=/data/models
VOLUME ["/data"]
EXPOSE 8000
CMD ["uv", "run", "mimic-server"]
```

`docker-compose.yml` in the repo provides a one-command server run with GPU passthrough and a named volume for `/data`.

## CI/CD

### `ci.yml` (on PRs + main)

- `./lint.sh` (ruff format, ruff check, mypy across server + client)
- Pytest (client unit tests with mocked HTTP transport)
- Build sanity check (uv build of client wheel)

### Lint configuration

`lint.sh` (adapted from lumbergh) iterates the workspace packages and runs, in order:

1. `uv run ruff format .`
2. `uv run ruff check --fix .`
3. `uv run mypy .`

Aggregates errors and exits non-zero if any step failed. Used by both `release.sh` preflight and CI.

Shared ruff configuration in the workspace root `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = [
    "A", "ARG", "B", "C4", "C90", "DTZ", "E", "F", "FAST", "FLY", "FURB",
    "I", "LOG", "N", "PERF", "PIE", "PT", "RET", "RUF", "S", "SIM", "TC", "UP", "W",
]
ignore = [
    "E501",    # line length handled by formatter
    "B008",    # function call in default argument (FastAPI Depends)
    "B904",    # raise from within except
    "S101",    # assert usage (fine in tests)
    "S104",    # bind to 0.0.0.0
    "S324",    # insecure hash (not used for crypto)
    "S603",    # subprocess call
    "S606",    # os.popen
    "S607",    # partial executable path
    "SIM105",  # contextlib.suppress
    "RUF012",  # mutable class attributes (pydantic models)
]

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.mypy]
python_version = "3.12"
warn_return_any = false
warn_unused_configs = true
disallow_untyped_defs = false
check_untyped_defs = false
ignore_missing_imports = true
```

The McCabe `max-complexity = 10` cap is the load-bearing rule: it forces the
recorder flow, model manager, and CLI command handlers to stay decomposed into
small, single-purpose functions rather than collapsing into one nested mess.

### `release.yml` (on push of `v*` tag)

A shared "prep" job, then three parallel jobs:

- **publish-pypi** — build the client wheel; publish via PyPI Trusted Publishing (OIDC, no API token stored).
- **publish-docker** — `docker buildx` and push to `ghcr.io/<owner>/mimic-tts:vX.Y.Z` and `:latest`. CUDA 12.1 only at v0.1.
- **github-release** — generate AI release notes from commits since previous tag (mirrors lumbergh; gracefully skipped if `LLM_API_KEY` secret unset); `gh release create` with the wheel attached.

### `release.sh` (adapted from lumbergh)

1. Preflight: clean working tree, no untracked files, lint passes.
2. Determine bump (`major|minor|patch`, default `patch`); compute new version from latest `v*` tag.
3. Rewrite version in both `client/pyproject.toml` and `server/pyproject.toml`.
4. Commit the bump, tag `vX.Y.Z`, push tag.
5. Monitor GitHub Actions via `gh run list --commit <sha>`; fail loudly on any job failure.

## Privacy / publish hygiene

- `reference/` already gitignored.
- Extend `.gitignore`: `.env`, `.env.local`, `*.wav` at repo root, `.venv/`, `dist/`, `*.egg-info/`, `__pycache__/`.
- Add `.dockerignore`: same set plus `.git/`, `reference/`, `docs/`, tests.
- Add `NOTICE` crediting Qwen3-TTS upstream.
- Pre-publish checklist (in `docs/self-hosting.md`): run `gitleaks` once before first push.
- README explicit privacy callout: reference recordings never leave the user's machine.

## Documentation

- **README.md** (landing) — one-paragraph what-it-is, two side-by-side quickstarts ("Run the server with Docker" / "Use the client"), links to deeper docs.
- **docs/server.md** — env-var table, endpoint reference, GPU/VRAM notes, idle-unload behavior, multi-language voice list.
- **docs/client.md** — install, CLI reference, library examples (sync + async), recording tips, troubleshooting.
- **docs/self-hosting.md** — docker-compose, GPU passthrough, reverse-proxy + auth setup, model cache volume, gitleaks pre-publish step.
- **CONTRIBUTING.md** — dev setup with `uv sync` workspace, running tests, release process.

## Out of scope (v0.1)

- WebUI / playground page.
- Streaming TTS responses (model returns full wav).
- Multi-tenant / per-user voices.
- Auth beyond a single static bearer token.
- ARM / CPU Docker builds.
- HTTPS / TLS termination (left to the user's reverse proxy).

## Architectural decisions

- **Monorepo over split repos:** keeps issues/docs/versions correlated, avoids doubled CI maintenance for a solo maintainer.
- **Single shared version for client + server:** simpler release flow; can split later if cadences diverge.
- **Optional auth, off by default:** matches typical local/LAN use; flips on with one env var when exposing publicly.
- **Sync + async client share `_BaseClient`:** small lift over sync-only, doubles the public surface but tests are mocked at the transport layer so cost is low.
- **Weights not baked into Docker image:** keeps the image small and license-clean; first run downloads to a mounted cache volume.
- **PyPI Trusted Publishing (OIDC):** no long-lived tokens stored in repo secrets.
