# Plan 1 — Server Refactor + Docker

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the repo to a monorepo, extract the existing `main.py` into a clean `mimic_server` package with env-driven config, optional bearer auth, and a model manager; ship a working Docker image.

**Architecture:** Convert the flat repo to a `uv` workspace. The server becomes `server/mimic_server/` with one responsibility per module (`config.py`, `models.py`, `auth.py`, `app.py`). All current endpoints stay shape-compatible. Docker image runs the new `mimic-server` console entry; weights download to a mounted `/data` volume on first use.

**Tech Stack:** Python 3.12, FastAPI, uv (workspace), pydantic-settings, pytest, ruff, mypy, Docker (CUDA 12.1 runtime base).

**Source of truth:** `docs/superpowers/specs/2026-04-26-mimic-tts-design.md`.

**Out of scope (this plan):** client package, CI workflows, `release.sh`, public-facing docs (README rewrite, `docs/*.md`). Those are Plans 2 and 3.

---

### Task 1: Workspace root + lint config + .gitignore/.dockerignore

**Files:**
- Modify: `pyproject.toml` (currently the flat single-package config — replace with workspace root)
- Create: `.dockerignore`
- Modify: `.gitignore`

Establish the uv workspace root and the shared ruff/mypy config. The current `pyproject.toml`, `uv.lock`, and `main.py` are about to be moved under `server/` in Task 2 — this task only touches the root and ignore files.

- [ ] **Step 1: Replace `pyproject.toml` with workspace root**

```toml
[project]
name = "mimic-tts-workspace"
version = "0.0.0"
description = "Workspace root for mimic-tts (server + client)"
requires-python = ">=3.12,<3.13"

[tool.uv.workspace]
members = ["server", "client"]

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

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

- [ ] **Step 2: Update `.gitignore`**

The existing rule `reference/*` only matches the legacy top-level path. After Task 2 moves the directory under `server/`, that rule stops matching. Replace the reference block and append the new entries.

Replace this block in `.gitignore`:

```
# Voice reference samples (personal data — don't commit recordings)
reference/*
!reference/.gitkeep
```

with:

```
# Voice reference samples (personal data — don't commit recordings)
server/reference/*
!server/reference/.gitkeep
reference/*
!reference/.gitkeep

# Build artifacts
dist/
*.egg-info/

# Local secrets / env
.env
.env.local

# IDE / OS junk
.DS_Store
.idea/
.vscode/

# Audio recordings outside the reference dir
/*.wav
```

(We keep both the `server/reference/*` and legacy `reference/*` rules so a stale `reference/` directory at the repo root — e.g. left behind during the move — still stays ignored.)

- [ ] **Step 3: Create `.dockerignore`**

```
.git/
.github/
.venv/
__pycache__/
*.py[oc]
build/
dist/
*.egg-info/
docs/
client/
reference/
*.wav
.env
.env.local
.DS_Store
```

- [ ] **Step 4: Verify uv accepts the workspace stub (cannot resolve yet — server/ doesn't exist)**

Run:
```bash
uv sync 2>&1 | head -5
```
Expected: an error like `failed to read workspace member: server` — this is fine, Task 2 creates `server/`. We're only verifying the file parses.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore .dockerignore
git commit -m "chore: convert root to uv workspace + shared lint config"
```

---

### Task 2: Move existing server into `server/mimic_server/`

**Files:**
- Move: `main.py` → `server/mimic_server/app.py`
- Move: `uv.lock` → `server/uv.lock`
- Move: `.python-version` → `server/.python-version`
- Move: `reference/` → `server/reference/` (keep `.gitkeep`)
- Create: `server/pyproject.toml`
- Create: `server/mimic_server/__init__.py`

Mechanical move: take the working server as-is and put it under `server/`. No code changes yet — just relocate so subsequent tasks can refactor in place.

- [ ] **Step 1: Create `server/` skeleton and move files**

```bash
mkdir -p server/mimic_server
git mv main.py server/mimic_server/app.py
git mv uv.lock server/uv.lock
git mv .python-version server/.python-version
git mv reference server/reference
touch server/mimic_server/__init__.py
```

- [ ] **Step 2: Create `server/pyproject.toml`**

```toml
[project]
name = "mimic-server"
version = "0.0.0"
description = "Self-hosted Qwen3-TTS server (mimic-tts)"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi[standard]",
    "qwen-tts",
    "torch>=2.5.0,<2.6.0",
    "torchaudio>=2.5.0,<2.6.0",
    "soundfile",
    "python-multipart",
    "pydantic-settings>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.6",
    "mypy>=1.10",
    "httpx",
]

[project.scripts]
mimic-server = "mimic_server.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["mimic_server"]

[tool.uv]
index-url = "https://pypi.org/simple"

[[tool.uv.index]]
name = "pytorch-cu121"
url = "https://download.pytorch.org/whl/cu121"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-cu121" }
torchaudio = { index = "pytorch-cu121" }
```

Note: `mimic_server.__main__:main` is created in Task 7 — the entry will fail until then, which is expected.

- [ ] **Step 3: Fix the reference path in `app.py`**

The existing `app.py` references `Path("reference")` which now resolves relative to wherever the server runs. Leave it alone for this task — Task 3 replaces it with a configurable path. We only confirm the move worked.

- [ ] **Step 4: Sync the workspace**

Run:
```bash
uv sync --package mimic-server
```
Expected: dependencies resolve and install. The `mimic-server` script entry will warn about the missing `__main__` — ignore.

- [ ] **Step 5: Smoke import the moved module**

Run:
```bash
uv run --package mimic-server python -c "from mimic_server import app; print(app.app.title)"
```
Expected: `Qwen3-TTS API`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: move server into server/mimic_server/ workspace package"
```

---

### Task 3: Add `config.py` with env-driven `Settings`

**Files:**
- Create: `server/mimic_server/config.py`
- Create: `server/tests/__init__.py`
- Create: `server/tests/test_config.py`

Centralize all `MIMIC_*` env vars in a `Settings` dataclass produced by pydantic-settings. Detects Docker mode via `MIMIC_DATA_DIR=/data` and applies Docker defaults.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_config.py`:

```python
from pathlib import Path

from mimic_server.config import Settings


def test_local_defaults(monkeypatch, tmp_path):
    for k in ["MIMIC_DATA_DIR", "MIMIC_HOST", "MIMIC_PORT", "MIMIC_REFERENCE_DIR",
              "MIMIC_MODEL_CACHE", "MIMIC_UNLOAD_AFTER", "MIMIC_API_TOKEN"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)

    s = Settings()

    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.reference_dir == Path("reference").resolve()
    assert s.model_cache is None
    assert s.unload_after == 15
    assert s.api_token is None
    assert s.log_level == "INFO"


def test_docker_defaults_when_data_dir_set(monkeypatch):
    monkeypatch.setenv("MIMIC_DATA_DIR", "/data")
    for k in ["MIMIC_HOST", "MIMIC_REFERENCE_DIR", "MIMIC_MODEL_CACHE"]:
        monkeypatch.delenv(k, raising=False)

    s = Settings()

    assert s.host == "0.0.0.0"
    assert s.reference_dir == Path("/data/reference")
    assert s.model_cache == Path("/data/models")


def test_explicit_env_overrides_docker_default(monkeypatch):
    monkeypatch.setenv("MIMIC_DATA_DIR", "/data")
    monkeypatch.setenv("MIMIC_HOST", "10.0.0.5")
    monkeypatch.setenv("MIMIC_REFERENCE_DIR", "/srv/voices")

    s = Settings()

    assert s.host == "10.0.0.5"
    assert s.reference_dir == Path("/srv/voices")


def test_api_token_round_trip(monkeypatch):
    monkeypatch.setenv("MIMIC_API_TOKEN", "shhh")
    s = Settings()
    assert s.api_token == "shhh"
    assert s.auth_required is True


def test_no_token_means_no_auth(monkeypatch):
    monkeypatch.delenv("MIMIC_API_TOKEN", raising=False)
    s = Settings()
    assert s.auth_required is False
```

- [ ] **Step 2: Run the test (should fail — no module yet)**

Run:
```bash
cd server && uv run pytest tests/test_config.py -v
```
Expected: ImportError on `mimic_server.config`.

- [ ] **Step 3: Implement `config.py`**

Create `server/mimic_server/config.py`:

```python
"""Environment-driven settings for the mimic-tts server."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_reference_dir() -> Path:
    data_dir = os.environ.get("MIMIC_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "reference"
    return Path("reference").resolve()


def _default_model_cache() -> Path | None:
    data_dir = os.environ.get("MIMIC_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "models"
    return None


def _default_host() -> str:
    return "0.0.0.0" if os.environ.get("MIMIC_DATA_DIR") else "127.0.0.1"  # noqa: S104


class Settings(BaseSettings):
    """All MIMIC_* env vars. Constructed once at app startup."""

    model_config = SettingsConfigDict(env_prefix="MIMIC_", extra="ignore")

    host: str = Field(default_factory=_default_host)
    port: int = 8000
    reference_dir: Path = Field(default_factory=_default_reference_dir)
    model_cache: Path | None = Field(default_factory=_default_model_cache)
    unload_after: int = 15
    api_token: str | None = None
    log_level: str = "INFO"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def auth_required(self) -> bool:
        return self.api_token is not None
```

- [ ] **Step 4: Run the tests until green**

Run:
```bash
cd server && uv run pytest tests/test_config.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add server/mimic_server/config.py server/tests/__init__.py server/tests/test_config.py
git commit -m "feat(server): add env-driven Settings (config.py)"
```

---

### Task 4: Extract model loading into `ModelManager`

**Files:**
- Create: `server/mimic_server/models.py`
- Create: `server/tests/test_models.py`

Pull the global model dict / lock / idle watcher into a class. Inject a `loader` callable so tests don't actually load Qwen3-TTS.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_models.py`:

```python
import asyncio
import time

import pytest

from mimic_server.models import ModelManager


class FakeModel:
    def __init__(self, name: str) -> None:
        self.name = name


def test_loads_on_demand_and_caches():
    calls: list[str] = []

    def loader(model_id: str) -> FakeModel:
        calls.append(model_id)
        return FakeModel(model_id)

    mm = ModelManager(loader=loader, unload_after=60)
    mm.register("clone", "Qwen/clone-id")

    a = mm.get("clone")
    b = mm.get("clone")

    assert a is b
    assert calls == ["Qwen/clone-id"]


def test_get_unknown_key_raises():
    mm = ModelManager(loader=lambda mid: FakeModel(mid), unload_after=60)
    with pytest.raises(KeyError):
        mm.get("custom")


def test_unload_all_clears_cache():
    mm = ModelManager(loader=lambda mid: FakeModel(mid), unload_after=60)
    mm.register("clone", "Qwen/c")
    mm.get("clone")
    mm.unload_all()
    assert mm.loaded_keys() == []


def test_status_reports_loaded_keys():
    mm = ModelManager(loader=lambda mid: FakeModel(mid), unload_after=60)
    mm.register("clone", "Qwen/c")
    mm.register("custom", "Qwen/cv")
    mm.get("clone")
    assert mm.loaded_keys() == ["clone"]


@pytest.mark.asyncio
async def test_idle_watcher_unloads_after_timeout():
    mm = ModelManager(loader=lambda mid: FakeModel(mid), unload_after=0.05)
    mm.register("clone", "Qwen/c")
    mm.get("clone")

    task = asyncio.create_task(mm.run_unload_watcher(poll_interval=0.01))
    try:
        await asyncio.sleep(0.2)
        assert mm.loaded_keys() == []
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_get_resets_idle_timer(monkeypatch):
    mm = ModelManager(loader=lambda mid: FakeModel(mid), unload_after=60)
    mm.register("clone", "Qwen/c")
    mm.get("clone")
    t0 = mm.last_used()
    time.sleep(0.01)
    mm.get("clone")
    assert mm.last_used() > t0
```

- [ ] **Step 2: Run the test (should fail — no module yet)**

Run:
```bash
cd server && uv run pytest tests/test_models.py -v
```
Expected: ImportError on `mimic_server.models`.

- [ ] **Step 3: Implement `models.py`**

Create `server/mimic_server/models.py`:

```python
"""Model load/unload manager. Decoupled from Qwen3-TTS for testability."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ModelManager(Generic[T]):
    """Caches loaded models keyed by short name; unloads after idle."""

    def __init__(
        self,
        loader: Callable[[str], T],
        unload_after: float,
        on_unload: Callable[[], None] | None = None,
    ) -> None:
        self._loader = loader
        self._unload_after = unload_after
        self._on_unload = on_unload or (lambda: None)
        self._registry: dict[str, str] = {}
        self._loaded: dict[str, T] = {}
        self._lock = threading.Lock()
        self._last_used = time.monotonic()

    def register(self, key: str, model_id: str) -> None:
        self._registry[key] = model_id

    def get(self, key: str) -> T:
        if key not in self._registry:
            raise KeyError(f"unknown model key: {key}")
        with self._lock:
            self._last_used = time.monotonic()
            if key not in self._loaded:
                self._loaded[key] = self._loader(self._registry[key])
            return self._loaded[key]

    def unload_all(self) -> None:
        with self._lock:
            if not self._loaded:
                return
            names = list(self._loaded)
            self._loaded.clear()
        self._on_unload()
        logger.info("unloaded models: %s", ", ".join(names))

    def loaded_keys(self) -> list[str]:
        with self._lock:
            return list(self._loaded)

    def last_used(self) -> float:
        return self._last_used

    async def run_unload_watcher(self, poll_interval: float = 5.0) -> None:
        while True:
            await asyncio.sleep(poll_interval)
            with self._lock:
                if not self._loaded:
                    continue
                idle = time.monotonic() - self._last_used
            if idle >= self._unload_after:
                self.unload_all()
```

- [ ] **Step 4: Run the tests until green**

Run:
```bash
cd server && uv run pytest tests/test_models.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add server/mimic_server/models.py server/tests/test_models.py
git commit -m "feat(server): extract ModelManager with idle-unload"
```

---

### Task 5: Add optional bearer-token auth dependency

**Files:**
- Create: `server/mimic_server/auth.py`
- Create: `server/tests/test_auth.py`

A FastAPI dependency that, when `settings.api_token` is set, requires `Authorization: Bearer <token>` on protected routes; otherwise allows all requests through.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_auth.py`:

```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from mimic_server.auth import require_token
from mimic_server.config import Settings


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    def protected(_: None = Depends(require_token(settings))) -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_no_token_configured_allows_anyone():
    settings = Settings(api_token=None)
    client = TestClient(_app(settings))
    assert client.get("/protected").status_code == 200


def test_token_required_rejects_missing_header():
    settings = Settings(api_token="shhh")
    client = TestClient(_app(settings))
    r = client.get("/protected")
    assert r.status_code == 401
    assert "Bearer" in r.headers.get("WWW-Authenticate", "")


def test_token_required_rejects_wrong_token():
    settings = Settings(api_token="shhh")
    client = TestClient(_app(settings))
    r = client.get("/protected", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_token_required_accepts_correct_token():
    settings = Settings(api_token="shhh")
    client = TestClient(_app(settings))
    r = client.get("/protected", headers={"Authorization": "Bearer shhh"})
    assert r.status_code == 200
```

- [ ] **Step 2: Run the test (should fail — no module yet)**

Run:
```bash
cd server && uv run pytest tests/test_auth.py -v
```
Expected: ImportError on `mimic_server.auth`.

- [ ] **Step 3: Implement `auth.py`**

Create `server/mimic_server/auth.py`:

```python
"""Optional bearer-token auth dependency."""
from __future__ import annotations

import secrets
from typing import Callable

from fastapi import Header, HTTPException, status

from mimic_server.config import Settings


def require_token(settings: Settings) -> Callable[..., None]:
    """Return a dependency. If no token is configured, dependency is a no-op."""

    if not settings.auth_required:
        def _noop() -> None:
            return None
        return _noop

    expected = settings.api_token or ""

    def _check(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing bearer token",
                headers={"WWW-Authenticate": 'Bearer realm="mimic"'},
            )
        token = authorization.removeprefix("Bearer ").strip()
        if not secrets.compare_digest(token, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": 'Bearer realm="mimic"'},
            )

    return _check
```

- [ ] **Step 4: Run the tests until green**

Run:
```bash
cd server && uv run pytest tests/test_auth.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add server/mimic_server/auth.py server/tests/test_auth.py
git commit -m "feat(server): add optional bearer-token auth dependency"
```

---

### Task 6: Refactor `app.py` to use `Settings`, `ModelManager`, and `require_token`

**Files:**
- Rewrite: `server/mimic_server/app.py`
- Create: `server/tests/test_app.py`

Wire the new modules together. Endpoints stay shape-compatible. The Qwen3-TTS loader stays in `app.py` (where torch is imported); `ModelManager` receives it as a callable.

- [ ] **Step 1: Write the failing test for routing + auth integration**

Create `server/tests/test_app.py`:

```python
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from mimic_server.app import build_app
from mimic_server.config import Settings


@pytest.fixture
def fake_model():
    m = MagicMock()
    m.generate_custom_voice.return_value = ([b"\x00\x01"], 24000)
    m.generate_voice_clone.return_value = ([b"\x00\x01"], 24000)
    m.create_voice_clone_prompt.return_value = object()
    return m


def test_health_no_auth(tmp_path, fake_model):
    settings = Settings(reference_dir=tmp_path, api_token=None)
    app = build_app(settings, model_loader=lambda mid: fake_model)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_voices_unauthenticated_when_no_token(tmp_path, fake_model):
    settings = Settings(reference_dir=tmp_path, api_token=None)
    app = build_app(settings, model_loader=lambda mid: fake_model)
    client = TestClient(app)
    assert client.get("/voices").status_code == 200


def test_protected_route_rejects_without_token(tmp_path, fake_model):
    settings = Settings(reference_dir=tmp_path, api_token="shhh")
    app = build_app(settings, model_loader=lambda mid: fake_model)
    client = TestClient(app)
    assert client.get("/voices").status_code == 401


def test_health_remains_open_even_with_token(tmp_path, fake_model):
    settings = Settings(reference_dir=tmp_path, api_token="shhh")
    app = build_app(settings, model_loader=lambda mid: fake_model)
    client = TestClient(app)
    assert client.get("/health").status_code == 200


def test_tts_endpoint_returns_wav(tmp_path, fake_model):
    import numpy as np
    fake_model.generate_custom_voice.return_value = ([np.zeros(1024, dtype=np.float32)], 24000)

    settings = Settings(reference_dir=tmp_path, api_token=None)
    app = build_app(settings, model_loader=lambda mid: fake_model)
    client = TestClient(app)

    r = client.post("/tts", data={"text": "hello", "speaker": "Ryan"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
```

- [ ] **Step 2: Run the test (should fail — no `build_app` yet)**

Run:
```bash
cd server && uv run pytest tests/test_app.py -v
```
Expected: ImportError on `build_app`.

- [ ] **Step 3: Rewrite `app.py`**

Replace `server/mimic_server/app.py` with:

```python
"""mimic-tts server — FastAPI app factory."""
from __future__ import annotations

import io
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import soundfile as sf
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from mimic_server.auth import require_token
from mimic_server.config import Settings
from mimic_server.models import ModelManager

logger = logging.getLogger(__name__)

CLONE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
CUSTOM_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"

BUILTIN_VOICES = [
    {"name": "Ryan", "language": "English"},
    {"name": "Aiden", "language": "English"},
    {"name": "Vivian", "language": "Chinese"},
    {"name": "Serena", "language": "Chinese"},
    {"name": "Uncle_Fu", "language": "Chinese"},
    {"name": "Dylan", "language": "Chinese"},
    {"name": "Eric", "language": "Chinese"},
    {"name": "Ono_Anna", "language": "Japanese"},
    {"name": "Sohee", "language": "Korean"},
]


def _default_qwen_loader(model_id: str) -> Any:
    import torch
    from qwen_tts import Qwen3TTSModel

    logger.info("loading %s …", model_id)
    t0 = time.monotonic()
    model = Qwen3TTSModel.from_pretrained(
        model_id, device_map="cuda:0", dtype=torch.bfloat16,
    )
    logger.info("loaded %s in %.1fs", model_id, time.monotonic() - t0)
    return model


def _on_torch_unload() -> None:
    try:
        import torch
        torch.cuda.empty_cache()
    except ImportError:
        pass


def _wav_response(samples, sample_rate: int, filename: str = "output.wav") -> StreamingResponse:
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="audio/wav",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def build_app(
    settings: Settings,
    model_loader: Callable[[str], Any] | None = None,
) -> FastAPI:
    """Construct the FastAPI app with injected settings and model loader."""

    logging.basicConfig(level=settings.log_level)
    if settings.model_cache is not None:
        import os
        os.environ["HF_HOME"] = str(settings.model_cache)

    settings.reference_dir.mkdir(parents=True, exist_ok=True)

    loader = model_loader or _default_qwen_loader
    mm: ModelManager[Any] = ModelManager(
        loader=loader,
        unload_after=settings.unload_after,
        on_unload=_on_torch_unload,
    )
    mm.register("clone", CLONE_MODEL_ID)
    mm.register("custom", CUSTOM_MODEL_ID)

    voice_prompts: dict[str, Any] = {}
    auth = Depends(require_token(settings))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        import asyncio
        task = asyncio.create_task(mm.run_unload_watcher())
        try:
            yield
        finally:
            task.cancel()
            mm.unload_all()

    app = FastAPI(title="mimic-tts API", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "models_loaded": mm.loaded_keys(),
            "registered_voices": list(voice_prompts),
        }

    @app.get("/voices", dependencies=[auth])
    async def list_voices() -> dict[str, list[dict[str, str]]]:
        return {"voices": BUILTIN_VOICES}

    @app.get("/clone/voices", dependencies=[auth])
    async def list_clone_voices() -> dict[str, list[str]]:
        on_disk = {p.parent.name for p in settings.reference_dir.glob("*/audio.wav")}
        return {"voices": sorted(on_disk | voice_prompts.keys())}

    @app.post("/tts", dependencies=[auth])
    async def tts(
        text: str = Form(...),
        language: str = Form("English"),
        speaker: str = Form("Ryan"),
        instruct: str = Form(""),
    ):
        model = mm.get("custom")
        wavs, sr = model.generate_custom_voice(
            text=text, language=language, speaker=speaker,
            instruct=instruct or None,
        )
        return _wav_response(wavs[0], sr)

    @app.post("/clone/register", dependencies=[auth])
    async def clone_register(
        ref_audio: UploadFile = File(...),
        ref_text: str = Form(...),
        name: str = Form("default"),
    ) -> dict[str, str]:
        model = mm.get("clone")
        audio_bytes = await ref_audio.read()
        ref_dir = settings.reference_dir / name
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / "audio.wav").write_bytes(audio_bytes)
        (ref_dir / "text.txt").write_text(ref_text)
        voice_prompts[name] = model.create_voice_clone_prompt(
            ref_audio=str(ref_dir / "audio.wav"), ref_text=ref_text,
        )
        return {"status": "ok", "name": name}

    @app.post("/clone/tts", dependencies=[auth])
    async def clone_tts(
        text: str = Form(...),
        language: str = Form("English"),
        name: str = Form("default"),
    ):
        model = mm.get("clone")
        if name not in voice_prompts:
            ref_path = settings.reference_dir / name / "audio.wav"
            ref_text_path = settings.reference_dir / name / "text.txt"
            if not (ref_path.exists() and ref_text_path.exists()):
                raise HTTPException(400, f"no voice '{name}' registered")
            voice_prompts[name] = model.create_voice_clone_prompt(
                ref_audio=str(ref_path), ref_text=ref_text_path.read_text(),
            )
        wavs, sr = model.generate_voice_clone(
            text=text, language=language, voice_clone_prompt=voice_prompts[name],
        )
        return _wav_response(wavs[0], sr)

    @app.post("/clone/oneshot", dependencies=[auth])
    async def clone_oneshot(
        text: str = Form(...),
        language: str = Form("English"),
        ref_audio: UploadFile = File(...),
        ref_text: str = Form(...),
    ):
        model = mm.get("clone")
        audio_bytes = await ref_audio.read()
        wavs, sr = model.generate_voice_clone(
            text=text, language=language,
            ref_audio=(io.BytesIO(audio_bytes), None), ref_text=ref_text,
        )
        return _wav_response(wavs[0], sr)

    return app


# Default app for `uvicorn mimic_server.app:app` and the console entry.
app = build_app(Settings())
```

- [ ] **Step 4: Run all server tests until green**

Run:
```bash
cd server && uv run pytest -v
```
Expected: all tests across `test_config`, `test_models`, `test_auth`, `test_app` pass.

- [ ] **Step 5: Lint**

Run:
```bash
cd server && uv run ruff check . && uv run ruff format --check .
```
Expected: no issues. Fix any complexity violations before committing.

- [ ] **Step 6: Commit**

```bash
git add server/mimic_server/app.py server/tests/test_app.py
git commit -m "refactor(server): wire app via build_app with injected settings/loader"
```

---

### Task 7: Add `mimic-server` console entry

**Files:**
- Create: `server/mimic_server/__main__.py`

The Dockerfile and `uv run mimic-server` both invoke this.

- [ ] **Step 1: Implement `__main__.py`**

Create `server/mimic_server/__main__.py`:

```python
"""Console entry: `mimic-server` runs uvicorn with env-driven settings."""
from __future__ import annotations

import uvicorn

from mimic_server.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "mimic_server.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the entry resolves**

Run:
```bash
cd server && uv sync && uv run mimic-server --help 2>&1 | head -1 || true
```
Expected: either uvicorn boots (then Ctrl-C) or you see uvicorn's startup line; the entry must at least resolve.

Better quick-check that doesn't bind a port:

```bash
cd server && uv run python -c "from mimic_server.__main__ import main; print('ok')"
```
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add server/mimic_server/__main__.py
git commit -m "feat(server): add mimic-server console entry"
```

---

### Task 8: Dockerfile

**Files:**
- Create: `server/Dockerfile`

Build context is the repo root (so the Dockerfile can `COPY server/...`).

- [ ] **Step 1: Create `server/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1.7
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3-pip libsndfile1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Workspace root + server pyproject for dependency-only caching layer.
# uv lockfile is at the workspace root in a uv workspace.
COPY pyproject.toml uv.lock ./
COPY server/pyproject.toml ./server/pyproject.toml
RUN uv sync --frozen --no-dev --package mimic-server --no-install-project

# Source
COPY server/mimic_server ./server/mimic_server
RUN uv sync --frozen --no-dev --package mimic-server

ENV MIMIC_DATA_DIR=/data \
    HF_HOME=/data/models \
    MIMIC_HOST=0.0.0.0 \
    MIMIC_PORT=8000

VOLUME ["/data"]
EXPOSE 8000

CMD ["uv", "run", "--package", "mimic-server", "mimic-server"]
```

- [ ] **Step 2: Lint the Dockerfile (if hadolint available; otherwise visually inspect)**

Run:
```bash
command -v hadolint >/dev/null && hadolint server/Dockerfile || echo "hadolint not installed; skipping"
```

- [ ] **Step 3: Build sanity check (CPU-only build, no GPU runtime needed)**

Run:
```bash
docker build -f server/Dockerfile -t mimic-tts:dev .
```
Expected: image builds cleanly. (Building does not require an NVIDIA GPU; only running does.)

If `docker` is not installed locally, skip Step 3 and note it as a manual verification step.

- [ ] **Step 4: Commit**

```bash
git add server/Dockerfile
git commit -m "feat(server): add Dockerfile (CUDA 12.1 runtime base)"
```

---

### Task 9: docker-compose example + README pointer

**Files:**
- Create: `docker-compose.yml` (repo root)
- Modify: `README.md` (add a temporary "Docker quickstart" pointer; full README rewrite is Plan 3)

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
services:
  mimic-server:
    build:
      context: .
      dockerfile: server/Dockerfile
    image: mimic-tts:dev
    ports:
      - "8000:8000"
    volumes:
      - mimic-data:/data
    environment:
      MIMIC_LOG_LEVEL: INFO
      # MIMIC_API_TOKEN: change-me   # uncomment to require bearer auth
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped

volumes:
  mimic-data:
```

- [ ] **Step 2: Append a Docker quickstart pointer to `README.md`**

Find the line in the existing README that says `## Install & run` and insert above it:

```markdown
## Quick start (Docker)

```bash
docker compose up --build
curl -X POST http://localhost:8000/tts -F 'text=Hello there.' --output out.wav
```

> Requires NVIDIA GPU + nvidia-container-toolkit. The full README is being rewritten as part of the mimic-tts rebrand.

```

(Plan 3 replaces the README wholesale; this is an interim pointer so the repo isn't misleading after Plan 1 ships.)

- [ ] **Step 3: Smoke-test compose config (does not require GPU)**

Run:
```bash
docker compose config >/dev/null
```
Expected: prints rendered config with no error.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml README.md
git commit -m "feat: add docker-compose.yml + interim Docker quickstart"
```

---

## End-of-plan verification

- [ ] All tests pass: `cd server && uv run pytest -v`
- [ ] Lint clean: `cd server && uv run ruff check . && uv run ruff format --check . && uv run mypy mimic_server`
- [ ] Manual smoke (requires GPU): `docker compose up --build` then `curl -F 'text=hi' http://localhost:8000/tts -o out.wav` and `aplay out.wav` (or equivalent)
- [ ] Auth smoke: set `MIMIC_API_TOKEN=shhh` in `docker-compose.yml`, restart, confirm `/voices` returns 401 without and 200 with `Authorization: Bearer shhh`

## State after this plan

- Repo is a uv workspace with `server/` populated and `client/` reserved (still empty).
- Dockerized server runs locally; GHCR push and CI come in Plan 3.
- `release.sh`, README rewrite, and docs are deferred to Plan 3.
- Client package work begins in Plan 2.
