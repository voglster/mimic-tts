# Plan 2 — Client Package (`mimic-tts` on PyPI)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `mimic-tts` client package: a sync `Client`, an async `AsyncClient`, environment + TOML config resolution, a guided microphone-recording flow, and a `mimic` CLI built on typer. After this plan, `pip install -e client` (locally) gives the user a `mimic` command and an importable Python library.

**Architecture:** Both sync and async clients share a `_BaseClient` for URL building, auth header injection, and error mapping; only the transport (`httpx.Client` vs `httpx.AsyncClient`) differs. Config resolves via `kwarg → env (MIMIC_*) → ~/.config/mimic/config.toml → defaults`. The recorder uses `sounddevice` for cross-platform mic capture and `soundfile` for WAV I/O. The CLI is a thin typer wrapper that constructs a `Client` and calls library methods.

**Tech Stack:** Python 3.12, httpx, typer, sounddevice, soundfile, numpy, platformdirs, pytest (with `pytest-mock` already installed in workspace), `httpx.MockTransport` for client tests, `respx` not required.

**Source of truth:** `docs/superpowers/specs/2026-04-26-mimic-tts-design.md`.

**Out of scope (this plan):** CI workflows, `release.sh`, PyPI publishing, README rewrite. Those are Plan 3.

---

### Task 1: Client package skeleton

**Files:**
- Create: `client/pyproject.toml`
- Create: `client/mimic/__init__.py`
- Create: `client/mimic/_version.py`
- Create: `client/tests/__init__.py`
- Create: `client/README.md` (minimal placeholder; full content in Plan 3)

Set up the package so subsequent tasks can fill in modules. The workspace root `pyproject.toml` already declares `members = ["server", "client"]` from Plan 1.

- [ ] **Step 1: Create `client/pyproject.toml`**

```toml
[project]
name = "mimic-tts"
version = "0.0.0"
description = "Client for mimic-tts (Qwen3-TTS voice cloning + synthesis)"
readme = "README.md"
requires-python = ">=3.12,<3.14"
license = { text = "MIT" }
authors = [{ name = "Jim Vogel" }]
dependencies = [
    "httpx>=0.27",
    "typer>=0.12",
    "sounddevice>=0.4",
    "soundfile>=0.12",
    "numpy>=1.26",
    "platformdirs>=4.0",
]

[project.scripts]
mimic = "mimic.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.version]
path = "mimic/_version.py"

[tool.hatch.build.targets.wheel]
packages = ["mimic"]
```

- [ ] **Step 2: Create `client/mimic/_version.py`**

```python
__version__ = "0.0.0"
```

- [ ] **Step 3: Create `client/mimic/__init__.py`**

For now this just exports the version. `Client` and `AsyncClient` are added in Tasks 3 and 4.

```python
"""mimic-tts — Python client for the mimic-tts server."""
from mimic._version import __version__

__all__ = ["__version__"]
```

- [ ] **Step 4: Create `client/tests/__init__.py`** (empty file)

- [ ] **Step 5: Create `client/README.md`** (minimal placeholder)

```markdown
# mimic-tts

Python client and CLI for the [mimic-tts](https://github.com/jvogel/mimic-tts) server.

```bash
pip install mimic-tts
```

Full documentation: <https://github.com/jvogel/mimic-tts>
```

- [ ] **Step 6: Sync the workspace and confirm install**

Run from the repo root:
```bash
uv sync --package mimic-tts
.venv/bin/python -c "import mimic; print(mimic.__version__)"
```
Expected: `0.0.0`.

Confirm the CLI script exists (it will fail to import until Task 7 creates `mimic.cli`, but the entry should be wired):
```bash
ls .venv/bin/mimic
```
Expected: file exists.

- [ ] **Step 7: Stage and commit**

```bash
git add client/
git commit -m "feat(client): scaffold mimic-tts package skeleton"
```

---

### Task 2: `_BaseClient` — shared request logic

**Files:**
- Create: `client/mimic/_base.py`
- Create: `client/mimic/errors.py`
- Create: `client/tests/test_base.py`

Encapsulates the bits that don't depend on sync-vs-async: URL construction, auth header injection, response → error translation, multipart form construction.

- [ ] **Step 1: Write the failing test**

Create `client/tests/test_base.py`:

```python
import pytest
import httpx

from mimic._base import RequestSpec, build_request_spec, raise_for_response
from mimic.errors import MimicAPIError, MimicAuthError, MimicNotFoundError


def test_build_get_request_no_auth():
    spec = build_request_spec(
        base_url="http://localhost:8000",
        method="GET",
        path="/voices",
        token=None,
    )
    assert spec.method == "GET"
    assert spec.url == "http://localhost:8000/voices"
    assert "authorization" not in {k.lower() for k in spec.headers}


def test_build_request_with_token_adds_bearer_header():
    spec = build_request_spec(
        base_url="http://localhost:8000",
        method="POST",
        path="/tts",
        token="shhh",
        data={"text": "hello"},
    )
    assert spec.headers["Authorization"] == "Bearer shhh"
    assert spec.data == {"text": "hello"}


def test_base_url_strips_trailing_slash():
    spec = build_request_spec(
        base_url="http://localhost:8000/",
        method="GET",
        path="/health",
        token=None,
    )
    assert spec.url == "http://localhost:8000/health"


def test_path_must_start_with_slash():
    with pytest.raises(ValueError):
        build_request_spec(
            base_url="http://localhost:8000",
            method="GET",
            path="health",
            token=None,
        )


def test_files_field_passed_through():
    spec = build_request_spec(
        base_url="http://localhost:8000",
        method="POST",
        path="/clone/register",
        token=None,
        data={"name": "alice", "ref_text": "hi"},
        files={"ref_audio": ("ref.wav", b"RIFF...", "audio/wav")},
    )
    assert spec.files == {"ref_audio": ("ref.wav", b"RIFF...", "audio/wav")}


def test_raise_for_response_401_raises_auth_error():
    response = httpx.Response(401, json={"detail": "missing bearer token"})
    with pytest.raises(MimicAuthError) as exc_info:
        raise_for_response(response)
    assert "missing bearer token" in str(exc_info.value)


def test_raise_for_response_404_raises_not_found():
    response = httpx.Response(404, json={"detail": "no voice 'alice' registered"})
    with pytest.raises(MimicNotFoundError):
        raise_for_response(response)


def test_raise_for_response_5xx_raises_generic_api_error():
    response = httpx.Response(500, text="boom")
    with pytest.raises(MimicAPIError) as exc_info:
        raise_for_response(response)
    assert exc_info.value.status_code == 500


def test_raise_for_response_2xx_does_nothing():
    response = httpx.Response(200, json={"ok": True})
    raise_for_response(response)  # should not raise


def test_request_spec_is_a_dataclass():
    spec = RequestSpec(method="GET", url="http://x/y", headers={}, data=None, files=None)
    assert spec.method == "GET"
```

- [ ] **Step 2: Run the test (should fail — no module yet)**

```bash
.venv/bin/pytest client/tests/test_base.py -v
```
Expected: ImportError on `mimic._base` and `mimic.errors`.

- [ ] **Step 3: Implement `client/mimic/errors.py`**

```python
"""Exception hierarchy for mimic-tts client errors."""
from __future__ import annotations


class MimicError(Exception):
    """Base class for all mimic-tts client errors."""


class MimicAPIError(MimicError):
    """Server returned a non-2xx response."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class MimicAuthError(MimicAPIError):
    """401: missing or invalid bearer token."""


class MimicNotFoundError(MimicAPIError):
    """404: requested resource (e.g. clone voice) does not exist."""


class MimicValidationError(MimicAPIError):
    """4xx other than 401/404: request was rejected as invalid."""
```

- [ ] **Step 4: Implement `client/mimic/_base.py`**

```python
"""Shared request-building and error-translation logic."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from mimic.errors import (
    MimicAPIError,
    MimicAuthError,
    MimicNotFoundError,
    MimicValidationError,
)


@dataclass
class RequestSpec:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    data: dict[str, Any] | None = None
    files: dict[str, Any] | None = None


def build_request_spec(
    *,
    base_url: str,
    method: str,
    path: str,
    token: str | None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> RequestSpec:
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")
    url = base_url.rstrip("/") + path
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return RequestSpec(method=method, url=url, headers=headers, data=data, files=files)


def _extract_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
    except Exception:
        pass
    return response.text or response.reason_phrase or ""


def raise_for_response(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = _extract_detail(response)
    if response.status_code == 401:
        raise MimicAuthError(response.status_code, detail)
    if response.status_code == 404:
        raise MimicNotFoundError(response.status_code, detail)
    if 400 <= response.status_code < 500:
        raise MimicValidationError(response.status_code, detail)
    raise MimicAPIError(response.status_code, detail)
```

- [ ] **Step 5: Run the tests until green**

```bash
.venv/bin/pytest client/tests/test_base.py -v
```
Expected: 10 passed.

- [ ] **Step 6: Commit**

```bash
git add client/mimic/_base.py client/mimic/errors.py client/tests/test_base.py
git commit -m "feat(client): add _BaseClient request/error primitives"
```

---

### Task 3: Sync `Client`

**Files:**
- Create: `client/mimic/client.py`
- Create: `client/tests/test_client.py`

Sync wrapper around `httpx.Client`. Public surface mirrors the design spec.

- [ ] **Step 1: Write the failing test**

Create `client/tests/test_client.py`:

```python
import io
from pathlib import Path

import httpx
import pytest

from mimic import Client
from mimic.errors import MimicAuthError, MimicNotFoundError


def _wav_bytes() -> bytes:
    # 1KB of zeros — enough to look like a wav body for the mock.
    return b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 1000


@pytest.fixture
def transport():
    """Programmable MockTransport. Tests register handlers on .routes."""
    routes: dict[tuple[str, str], httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in routes:
            return routes[key]
        return httpx.Response(404, json={"detail": f"unmocked {key}"})

    return httpx.MockTransport(handler), routes


def test_health(transport):
    mt, routes = transport
    routes[("GET", "/health")] = httpx.Response(
        200, json={"status": "ok", "models_loaded": [], "registered_voices": []},
    )
    c = Client(server_url="http://x", transport=mt)
    assert c.health()["status"] == "ok"


def test_list_voices(transport):
    mt, routes = transport
    routes[("GET", "/voices")] = httpx.Response(
        200, json={"voices": [{"name": "Ryan", "language": "English"}]},
    )
    c = Client(server_url="http://x", transport=mt)
    assert c.list_voices() == [{"name": "Ryan", "language": "English"}]


def test_list_clones(transport):
    mt, routes = transport
    routes[("GET", "/clone/voices")] = httpx.Response(
        200, json={"voices": ["alice", "bob"]},
    )
    c = Client(server_url="http://x", transport=mt)
    assert c.list_clones() == ["alice", "bob"]


def test_tts_returns_wav_bytes(transport):
    mt, routes = transport
    routes[("POST", "/tts")] = httpx.Response(
        200, content=_wav_bytes(), headers={"content-type": "audio/wav"},
    )
    c = Client(server_url="http://x", transport=mt)
    audio = c.tts("hello", speaker="Ryan")
    assert audio.startswith(b"RIFF")


def test_tts_to_file_writes_wav(transport, tmp_path):
    mt, routes = transport
    routes[("POST", "/tts")] = httpx.Response(
        200, content=_wav_bytes(), headers={"content-type": "audio/wav"},
    )
    c = Client(server_url="http://x", transport=mt)
    out = tmp_path / "out.wav"
    c.tts_to_file("hello", out, speaker="Ryan")
    assert out.read_bytes().startswith(b"RIFF")


def test_clone_register_with_path(transport, tmp_path):
    mt, routes = transport
    routes[("POST", "/clone/register")] = httpx.Response(
        200, json={"status": "ok", "name": "alice"},
    )
    c = Client(server_url="http://x", transport=mt)
    audio = tmp_path / "ref.wav"
    audio.write_bytes(_wav_bytes())
    result = c.clone_register("alice", audio, "transcript text")
    assert result == {"status": "ok", "name": "alice"}


def test_clone_register_with_bytes(transport):
    mt, routes = transport
    routes[("POST", "/clone/register")] = httpx.Response(
        200, json={"status": "ok", "name": "alice"},
    )
    c = Client(server_url="http://x", transport=mt)
    result = c.clone_register("alice", _wav_bytes(), "transcript")
    assert result["name"] == "alice"


def test_clone_tts(transport):
    mt, routes = transport
    routes[("POST", "/clone/tts")] = httpx.Response(
        200, content=_wav_bytes(), headers={"content-type": "audio/wav"},
    )
    c = Client(server_url="http://x", transport=mt)
    audio = c.clone_tts("alice", "hi")
    assert audio.startswith(b"RIFF")


def test_clone_oneshot(transport, tmp_path):
    mt, routes = transport
    routes[("POST", "/clone/oneshot")] = httpx.Response(
        200, content=_wav_bytes(), headers={"content-type": "audio/wav"},
    )
    c = Client(server_url="http://x", transport=mt)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(_wav_bytes())
    audio = c.clone_oneshot("hi", ref, "ref transcript")
    assert audio.startswith(b"RIFF")


def test_401_raises_auth_error(transport):
    mt, routes = transport
    routes[("GET", "/voices")] = httpx.Response(401, json={"detail": "no token"})
    c = Client(server_url="http://x", transport=mt)
    with pytest.raises(MimicAuthError):
        c.list_voices()


def test_404_clone_tts_raises_not_found(transport):
    mt, routes = transport
    routes[("POST", "/clone/tts")] = httpx.Response(
        400, json={"detail": "no voice 'ghost' registered"},
    )
    c = Client(server_url="http://x", transport=mt)
    # 400 maps to MimicValidationError, not MimicNotFoundError — verify
    from mimic.errors import MimicValidationError
    with pytest.raises(MimicValidationError):
        c.clone_tts("ghost", "hi")


def test_token_passed_in_authorization_header(transport):
    mt, routes = transport
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"voices": []})

    mt2 = httpx.MockTransport(handler)
    c = Client(server_url="http://x", token="shhh", transport=mt2)
    c.list_voices()
    assert seen["auth"] == "Bearer shhh"


def test_context_manager_closes_transport():
    closed = {"v": False}

    class TrackingTransport(httpx.MockTransport):
        def close(self) -> None:
            closed["v"] = True
            super().close()

    t = TrackingTransport(lambda r: httpx.Response(200, json={"voices": []}))
    with Client(server_url="http://x", transport=t):
        pass
    assert closed["v"] is True
```

- [ ] **Step 2: Run the test (should fail — no module yet)**

```bash
.venv/bin/pytest client/tests/test_client.py -v
```
Expected: ImportError on `Client`.

- [ ] **Step 3: Implement `client/mimic/client.py`**

```python
"""Synchronous client for the mimic-tts server."""
from __future__ import annotations

import os
from io import BufferedReader
from pathlib import Path
from typing import Any

import httpx

from mimic._base import build_request_spec, raise_for_response


class Client:
    """Sync client. Use as a context manager to ensure the transport closes."""

    def __init__(
        self,
        server_url: str | None = None,
        token: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = (server_url or os.environ.get("MIMIC_SERVER_URL")
                          or "http://localhost:8000")
        self._token = token if token is not None else os.environ.get("MIMIC_API_TOKEN")
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ── HTTP helpers ────────────────────────────────────────────────────

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        spec = build_request_spec(
            base_url=self._base_url, method=method, path=path,
            token=self._token, **kwargs,
        )
        r = self._http.request(spec.method, spec.url, headers=spec.headers,
                               data=spec.data, files=spec.files)
        raise_for_response(r)
        return r.json()

    def _request_audio(self, method: str, path: str, **kwargs: Any) -> bytes:
        spec = build_request_spec(
            base_url=self._base_url, method=method, path=path,
            token=self._token, **kwargs,
        )
        r = self._http.request(spec.method, spec.url, headers=spec.headers,
                               data=spec.data, files=spec.files)
        raise_for_response(r)
        return r.content

    # ── Public API ──────────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        return self._request_json("GET", "/health")

    def list_voices(self) -> list[dict[str, str]]:
        return self._request_json("GET", "/voices")["voices"]

    def list_clones(self) -> list[str]:
        return self._request_json("GET", "/clone/voices")["voices"]

    def tts(
        self, text: str, *, language: str = "English",
        speaker: str = "Ryan", instruct: str = "",
    ) -> bytes:
        return self._request_audio(
            "POST", "/tts",
            data={"text": text, "language": language,
                  "speaker": speaker, "instruct": instruct},
        )

    def tts_to_file(self, text: str, out: Path | str, **kwargs: Any) -> Path:
        audio = self.tts(text, **kwargs)
        out_path = Path(out)
        out_path.write_bytes(audio)
        return out_path

    def clone_register(
        self, name: str, audio: Path | str | bytes | BufferedReader, transcript: str,
    ) -> dict[str, str]:
        files = {"ref_audio": _as_upload(audio)}
        return self._request_json(
            "POST", "/clone/register",
            data={"name": name, "ref_text": transcript}, files=files,
        )

    def clone_tts(
        self, name: str, text: str, *, language: str = "English",
    ) -> bytes:
        return self._request_audio(
            "POST", "/clone/tts",
            data={"text": text, "language": language, "name": name},
        )

    def clone_oneshot(
        self, text: str, audio: Path | str | bytes | BufferedReader,
        transcript: str, *, language: str = "English",
    ) -> bytes:
        files = {"ref_audio": _as_upload(audio)}
        return self._request_audio(
            "POST", "/clone/oneshot",
            data={"text": text, "language": language, "ref_text": transcript},
            files=files,
        )


def _as_upload(audio: Path | str | bytes | BufferedReader) -> tuple[str, Any, str]:
    """Normalize audio inputs to a (filename, fileobj-or-bytes, content-type) tuple."""
    if isinstance(audio, (str, Path)):
        path = Path(audio)
        return (path.name, path.read_bytes(), "audio/wav")
    if isinstance(audio, bytes):
        return ("ref.wav", audio, "audio/wav")
    return ("ref.wav", audio, "audio/wav")
```

- [ ] **Step 4: Update `client/mimic/__init__.py` to export `Client`**

Replace contents with:

```python
"""mimic-tts — Python client for the mimic-tts server."""
from mimic._version import __version__
from mimic.client import Client

__all__ = ["Client", "__version__"]
```

- [ ] **Step 5: Run the tests until green**

```bash
.venv/bin/pytest client/tests/test_client.py -v
```
Expected: 13 passed.

- [ ] **Step 6: Commit**

```bash
git add client/mimic/client.py client/mimic/__init__.py client/tests/test_client.py
git commit -m "feat(client): add sync Client with full TTS + clone API"
```

---

### Task 4: `AsyncClient`

**Files:**
- Create: `client/mimic/async_client.py`
- Create: `client/tests/test_async_client.py`

Mirror of `Client` but using `httpx.AsyncClient`. Same public surface, awaitable.

- [ ] **Step 1: Write the failing test**

Create `client/tests/test_async_client.py`:

```python
import httpx
import pytest

from mimic import AsyncClient
from mimic.errors import MimicAuthError


def _wav_bytes() -> bytes:
    return b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 1000


@pytest.fixture
def transport():
    routes: dict[tuple[str, str], httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in routes:
            return routes[key]
        return httpx.Response(404, json={"detail": f"unmocked {key}"})

    return httpx.MockTransport(handler), routes


@pytest.mark.asyncio
async def test_health(transport):
    mt, routes = transport
    routes[("GET", "/health")] = httpx.Response(200, json={"status": "ok"})
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        result = await c.health()
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_list_voices(transport):
    mt, routes = transport
    routes[("GET", "/voices")] = httpx.Response(
        200, json={"voices": [{"name": "Ryan", "language": "English"}]},
    )
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        voices = await c.list_voices()
    assert voices[0]["name"] == "Ryan"


@pytest.mark.asyncio
async def test_tts(transport):
    mt, routes = transport
    routes[("POST", "/tts")] = httpx.Response(
        200, content=_wav_bytes(), headers={"content-type": "audio/wav"},
    )
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        audio = await c.tts("hi")
    assert audio.startswith(b"RIFF")


@pytest.mark.asyncio
async def test_clone_register(transport, tmp_path):
    mt, routes = transport
    routes[("POST", "/clone/register")] = httpx.Response(
        200, json={"status": "ok", "name": "alice"},
    )
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        ref = tmp_path / "ref.wav"
        ref.write_bytes(_wav_bytes())
        result = await c.clone_register("alice", ref, "transcript")
    assert result["name"] == "alice"


@pytest.mark.asyncio
async def test_clone_tts(transport):
    mt, routes = transport
    routes[("POST", "/clone/tts")] = httpx.Response(
        200, content=_wav_bytes(), headers={"content-type": "audio/wav"},
    )
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        audio = await c.clone_tts("alice", "hi")
    assert audio.startswith(b"RIFF")


@pytest.mark.asyncio
async def test_401_raises_auth_error(transport):
    mt, routes = transport
    routes[("GET", "/voices")] = httpx.Response(401, json={"detail": "no token"})
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        with pytest.raises(MimicAuthError):
            await c.list_voices()


@pytest.mark.asyncio
async def test_token_in_header():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"voices": []})

    mt = httpx.MockTransport(handler)
    async with AsyncClient(server_url="http://x", token="shhh", transport=mt) as c:
        await c.list_voices()
    assert seen["auth"] == "Bearer shhh"
```

- [ ] **Step 2: Run the test (should fail — no module yet)**

```bash
.venv/bin/pytest client/tests/test_async_client.py -v
```
Expected: ImportError on `AsyncClient`.

- [ ] **Step 3: Implement `client/mimic/async_client.py`**

```python
"""Asynchronous client for the mimic-tts server."""
from __future__ import annotations

import os
from io import BufferedReader
from pathlib import Path
from typing import Any

import httpx

from mimic._base import build_request_spec, raise_for_response
from mimic.client import _as_upload


class AsyncClient:
    """Async client. Use as `async with AsyncClient(...) as c:`."""

    def __init__(
        self,
        server_url: str | None = None,
        token: str | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = (server_url or os.environ.get("MIMIC_SERVER_URL")
                          or "http://localhost:8000")
        self._token = token if token is not None else os.environ.get("MIMIC_API_TOKEN")
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    # ── HTTP helpers ────────────────────────────────────────────────────

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        spec = build_request_spec(
            base_url=self._base_url, method=method, path=path,
            token=self._token, **kwargs,
        )
        r = await self._http.request(
            spec.method, spec.url, headers=spec.headers,
            data=spec.data, files=spec.files,
        )
        raise_for_response(r)
        return r.json()

    async def _request_audio(self, method: str, path: str, **kwargs: Any) -> bytes:
        spec = build_request_spec(
            base_url=self._base_url, method=method, path=path,
            token=self._token, **kwargs,
        )
        r = await self._http.request(
            spec.method, spec.url, headers=spec.headers,
            data=spec.data, files=spec.files,
        )
        raise_for_response(r)
        return r.content

    # ── Public API ──────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        return await self._request_json("GET", "/health")

    async def list_voices(self) -> list[dict[str, str]]:
        return (await self._request_json("GET", "/voices"))["voices"]

    async def list_clones(self) -> list[str]:
        return (await self._request_json("GET", "/clone/voices"))["voices"]

    async def tts(
        self, text: str, *, language: str = "English",
        speaker: str = "Ryan", instruct: str = "",
    ) -> bytes:
        return await self._request_audio(
            "POST", "/tts",
            data={"text": text, "language": language,
                  "speaker": speaker, "instruct": instruct},
        )

    async def tts_to_file(self, text: str, out: Path | str, **kwargs: Any) -> Path:
        audio = await self.tts(text, **kwargs)
        out_path = Path(out)
        out_path.write_bytes(audio)
        return out_path

    async def clone_register(
        self, name: str, audio: Path | str | bytes | BufferedReader, transcript: str,
    ) -> dict[str, str]:
        files = {"ref_audio": _as_upload(audio)}
        return await self._request_json(
            "POST", "/clone/register",
            data={"name": name, "ref_text": transcript}, files=files,
        )

    async def clone_tts(
        self, name: str, text: str, *, language: str = "English",
    ) -> bytes:
        return await self._request_audio(
            "POST", "/clone/tts",
            data={"text": text, "language": language, "name": name},
        )

    async def clone_oneshot(
        self, text: str, audio: Path | str | bytes | BufferedReader,
        transcript: str, *, language: str = "English",
    ) -> bytes:
        files = {"ref_audio": _as_upload(audio)}
        return await self._request_audio(
            "POST", "/clone/oneshot",
            data={"text": text, "language": language, "ref_text": transcript},
            files=files,
        )
```

- [ ] **Step 4: Update `client/mimic/__init__.py` to export `AsyncClient`**

```python
"""mimic-tts — Python client for the mimic-tts server."""
from mimic._version import __version__
from mimic.async_client import AsyncClient
from mimic.client import Client

__all__ = ["AsyncClient", "Client", "__version__"]
```

- [ ] **Step 5: Run the tests until green**

```bash
.venv/bin/pytest client/tests/test_async_client.py -v
```
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add client/mimic/async_client.py client/mimic/__init__.py client/tests/test_async_client.py
git commit -m "feat(client): add AsyncClient mirroring sync Client"
```

---

### Task 5: Config resolution

**Files:**
- Create: `client/mimic/config.py`
- Create: `client/tests/test_client_config.py`

`flag → env (MIMIC_*) → ~/.config/mimic/config.toml → defaults`. The CLI uses this; the library lets users pass `server_url`/`token` directly so a Settings type is overkill. Keep it as a single `load_config()` function returning a typed dict.

- [ ] **Step 1: Write the failing test**

Create `client/tests/test_client_config.py`:

```python
import textwrap

import pytest

from mimic.config import ClientConfig, load_config


def test_defaults_only(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMIC_SERVER_URL", raising=False)
    monkeypatch.delenv("MIMIC_API_TOKEN", raising=False)
    cfg = load_config(config_dir=tmp_path)
    assert cfg.server_url == "http://localhost:8000"
    assert cfg.token is None
    assert cfg.default_voice == "Ryan"


def test_env_overrides_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMIC_SERVER_URL", "http://nas.local:8000")
    monkeypatch.setenv("MIMIC_API_TOKEN", "shhh")
    cfg = load_config(config_dir=tmp_path)
    assert cfg.server_url == "http://nas.local:8000"
    assert cfg.token == "shhh"


def test_toml_used_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMIC_SERVER_URL", raising=False)
    monkeypatch.delenv("MIMIC_API_TOKEN", raising=False)
    (tmp_path / "config.toml").write_text(textwrap.dedent("""
        server_url = "http://nas.local:8000"
        token = "from-toml"
        default_voice = "Aiden"
    """))
    cfg = load_config(config_dir=tmp_path)
    assert cfg.server_url == "http://nas.local:8000"
    assert cfg.token == "from-toml"
    assert cfg.default_voice == "Aiden"


def test_env_overrides_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMIC_SERVER_URL", "http://from-env:8000")
    (tmp_path / "config.toml").write_text('server_url = "http://from-toml:8000"\n')
    cfg = load_config(config_dir=tmp_path)
    assert cfg.server_url == "http://from-env:8000"


def test_kwargs_override_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMIC_SERVER_URL", "http://from-env:8000")
    (tmp_path / "config.toml").write_text('server_url = "http://from-toml:8000"\n')
    cfg = load_config(
        server_url="http://from-arg:8000", token="from-arg", config_dir=tmp_path,
    )
    assert cfg.server_url == "http://from-arg:8000"
    assert cfg.token == "from-arg"


def test_malformed_toml_raises(tmp_path):
    (tmp_path / "config.toml").write_text("not = valid = toml\n")
    with pytest.raises(ValueError, match="invalid TOML"):
        load_config(config_dir=tmp_path)


def test_unknown_toml_keys_ignored(tmp_path):
    (tmp_path / "config.toml").write_text(textwrap.dedent("""
        server_url = "http://x:8000"
        unknown_key = "ignored"
    """))
    cfg = load_config(config_dir=tmp_path)
    assert cfg.server_url == "http://x:8000"


def test_client_config_is_a_dataclass():
    cfg = ClientConfig(server_url="http://x", token=None, default_voice="Ryan")
    assert cfg.server_url == "http://x"
```

- [ ] **Step 2: Run the test (should fail — no module yet)**

```bash
.venv/bin/pytest client/tests/test_client_config.py -v
```
Expected: ImportError on `mimic.config`.

- [ ] **Step 3: Implement `client/mimic/config.py`**

```python
"""Client configuration: kwarg → env → TOML → defaults."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path

DEFAULT_SERVER_URL = "http://localhost:8000"
DEFAULT_VOICE = "Ryan"

_KNOWN_TOML_KEYS = frozenset({"server_url", "token", "default_voice"})


@dataclass
class ClientConfig:
    server_url: str
    token: str | None
    default_voice: str


def _config_dir() -> Path:
    return user_config_path("mimic", appauthor=False)


def _read_toml(config_dir: Path) -> dict[str, object]:
    path = config_dir / "config.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"invalid TOML at {path}: {e}") from e
    return {k: v for k, v in data.items() if k in _KNOWN_TOML_KEYS}


def load_config(
    *,
    server_url: str | None = None,
    token: str | None = None,
    default_voice: str | None = None,
    config_dir: Path | None = None,
) -> ClientConfig:
    """Resolve config: kwarg → env → TOML → defaults."""
    file_data = _read_toml(config_dir or _config_dir())

    resolved_url = (
        server_url
        or os.environ.get("MIMIC_SERVER_URL")
        or file_data.get("server_url")
        or DEFAULT_SERVER_URL
    )
    resolved_token = (
        token
        if token is not None
        else os.environ.get("MIMIC_API_TOKEN") or file_data.get("token") or None
    )
    resolved_voice = (
        default_voice
        or file_data.get("default_voice")
        or DEFAULT_VOICE
    )

    return ClientConfig(
        server_url=str(resolved_url),
        token=str(resolved_token) if resolved_token is not None else None,
        default_voice=str(resolved_voice),
    )
```

- [ ] **Step 4: Run the tests until green**

```bash
.venv/bin/pytest client/tests/test_client_config.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add client/mimic/config.py client/tests/test_client_config.py
git commit -m "feat(client): add config resolution (kwarg → env → TOML → defaults)"
```

---

### Task 6: Recorder

**Files:**
- Create: `client/mimic/recorder.py`
- Create: `client/tests/test_recorder.py`

The interactive parts (prompts, audio I/O) are hard to test directly, so the module is split: pure functions for script selection and confirmation flow, with `sounddevice`/`soundfile` calls isolated in thin wrappers that tests can monkeypatch.

- [ ] **Step 1: Write the failing test**

Create `client/tests/test_recorder.py`:

```python
import io
from unittest.mock import MagicMock

import numpy as np
import pytest

from mimic.recorder import (
    PROMPT_SCRIPTS,
    RecordingResult,
    pick_script,
    record_until_enter,
    save_wav,
)


def test_pick_script_returns_one_of_the_known_scripts():
    s = pick_script(rng=__import__("random").Random(0))
    assert s in PROMPT_SCRIPTS
    assert len(s.split()) >= 5  # not empty


def test_pick_script_is_deterministic_with_seeded_rng():
    import random
    a = pick_script(rng=random.Random(42))
    b = pick_script(rng=random.Random(42))
    assert a == b


def test_record_until_enter_collects_audio_until_signal_set(monkeypatch):
    """The recorder reads audio chunks and concatenates them until a stop signal fires."""
    fake_chunks = [
        np.array([[0.1], [0.2]], dtype=np.float32),
        np.array([[0.3], [0.4]], dtype=np.float32),
    ]

    class FakeStream:
        def __init__(self, *args, **kwargs):
            self.callback = kwargs["callback"]

        def __enter__(self):
            for chunk in fake_chunks:
                self.callback(chunk, len(chunk), None, None)
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("mimic.recorder.sd.InputStream", FakeStream)

    stop = MagicMock()
    stop.wait.return_value = None

    result = record_until_enter(
        sample_rate=24000,
        channels=1,
        max_seconds=30,
        stop_event=stop,
    )

    assert isinstance(result, RecordingResult)
    assert result.sample_rate == 24000
    assert result.channels == 1
    assert result.audio.shape == (4, 1)
    np.testing.assert_allclose(
        result.audio.flatten(), [0.1, 0.2, 0.3, 0.4], rtol=1e-5,
    )


def test_save_wav_writes_a_readable_wav(tmp_path):
    audio = np.zeros((24000, 1), dtype=np.float32)
    out = tmp_path / "out.wav"
    save_wav(out, audio, sample_rate=24000)
    import soundfile as sf
    data, sr = sf.read(out)
    assert sr == 24000
    assert len(data) == 24000


def test_save_wav_to_buffer():
    audio = np.zeros((1000, 1), dtype=np.float32)
    buf = io.BytesIO()
    save_wav(buf, audio, sample_rate=24000)
    buf.seek(0)
    import soundfile as sf
    data, sr = sf.read(buf)
    assert sr == 24000
```

- [ ] **Step 2: Run the test (should fail — no module yet)**

```bash
.venv/bin/pytest client/tests/test_recorder.py -v
```
Expected: ImportError on `mimic.recorder`.

- [ ] **Step 3: Implement `client/mimic/recorder.py`**

```python
"""Microphone recording flow for the `mimic record` CLI command.

Pure helpers (script selection, save_wav) are independently testable.
The interactive flow lives in `record_with_prompts`.
"""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import numpy as np
import sounddevice as sd
import soundfile as sf

PROMPT_SCRIPTS: tuple[str, ...] = (
    "The quick brown fox jumps over the lazy dog while a thunderstorm rolls in.",
    "Could you please bring me a glass of water and a small slice of bread?",
    "Each spring the cherry trees blossom and turn the entire park into a sea of pink.",
    "I'd like to visit the museum tomorrow afternoon if the weather stays clear.",
    "Numbers like seventy-three and one hundred and forty-two are surprisingly tricky to say.",
    "She whispered carefully so that no one in the dim hallway would hear them speak.",
)

DEFAULT_SAMPLE_RATE = 24000
DEFAULT_CHANNELS = 1


@dataclass
class RecordingResult:
    audio: np.ndarray
    sample_rate: int
    channels: int


def pick_script(rng: random.Random | None = None) -> str:
    """Pick a random prompt script. Pass a seeded `Random` for determinism."""
    return (rng or random).choice(PROMPT_SCRIPTS)


def record_until_enter(
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    max_seconds: float = 30.0,
    stop_event: threading.Event | None = None,
) -> RecordingResult:
    """Capture audio from the default mic until `stop_event` fires or max_seconds elapses.

    The callback path is what tests exercise; in the CLI, a separate thread waits on
    stdin and sets `stop_event`.
    """
    stop = stop_event or threading.Event()
    chunks: list[np.ndarray] = []

    def callback(indata: np.ndarray, frames: int, time_info, status) -> None:
        chunks.append(indata.copy())

    with sd.InputStream(
        samplerate=sample_rate, channels=channels, callback=callback,
    ):
        stop.wait(timeout=max_seconds)

    audio = np.concatenate(chunks, axis=0) if chunks else np.zeros((0, channels), np.float32)
    return RecordingResult(audio=audio, sample_rate=sample_rate, channels=channels)


def save_wav(out: Path | str | IO[bytes], audio: np.ndarray, *, sample_rate: int) -> None:
    """Write a (frames, channels) float32 array as a WAV."""
    sf.write(out, audio, sample_rate, format="WAV", subtype="PCM_16")


def play(audio: np.ndarray, sample_rate: int) -> None:
    """Block until playback finishes. Used by the CLI for review."""
    sd.play(audio, samplerate=sample_rate)
    sd.wait()
```

- [ ] **Step 4: Run the tests until green**

```bash
.venv/bin/pytest client/tests/test_recorder.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add client/mimic/recorder.py client/tests/test_recorder.py
git commit -m "feat(client): add mic recorder primitives + script picker"
```

---

### Task 7: CLI

**Files:**
- Create: `client/mimic/cli.py`
- Create: `client/tests/test_cli.py`

Typer app exposing `say`, `record`, `clone say`, `voices`, `clones`, `config`, `health`. The interactive `record` flow is split: a `_run_recording_flow` function is unit-tested with stubs; the typer command wires real I/O.

- [ ] **Step 1: Write the failing test**

Create `client/tests/test_cli.py`:

```python
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from typer.testing import CliRunner

from mimic.cli import app
from mimic.recorder import RecordingResult


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    """All tests run with a fresh empty config dir so user-level files don't leak in."""
    monkeypatch.delenv("MIMIC_SERVER_URL", raising=False)
    monkeypatch.delenv("MIMIC_API_TOKEN", raising=False)
    monkeypatch.setenv("MIMIC_CONFIG_DIR", str(tmp_path))


def test_voices_lists_built_in(runner):
    fake = MagicMock()
    fake.list_voices.return_value = [
        {"name": "Ryan", "language": "English"},
        {"name": "Aiden", "language": "English"},
    ]
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["voices"])
    assert r.exit_code == 0
    assert "Ryan" in r.stdout
    assert "Aiden" in r.stdout


def test_clones_lists_registered(runner):
    fake = MagicMock()
    fake.list_clones.return_value = ["alice", "bob"]
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["clones"])
    assert r.exit_code == 0
    assert "alice" in r.stdout
    assert "bob" in r.stdout


def test_health(runner):
    fake = MagicMock()
    fake.health.return_value = {"status": "ok", "models_loaded": ["clone"], "registered_voices": []}
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["health"])
    assert r.exit_code == 0
    assert "ok" in r.stdout


def test_say_writes_output_file(runner, tmp_path):
    fake = MagicMock()
    fake.tts.return_value = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 100
    out = tmp_path / "out.wav"
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["say", "hello", "--out", str(out)])
    assert r.exit_code == 0
    assert out.exists()
    fake.tts.assert_called_once()


def test_say_default_voice_from_config(runner, tmp_path):
    fake = MagicMock()
    fake.tts.return_value = b"RIFF" + b"\x00" * 100
    out = tmp_path / "out.wav"
    (tmp_path / "config.toml").write_text('default_voice = "Aiden"\n')
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["say", "hello", "--out", str(out)])
    assert r.exit_code == 0
    fake.tts.assert_called_once()
    kwargs = fake.tts.call_args.kwargs
    assert kwargs["speaker"] == "Aiden"


def test_clone_say(runner, tmp_path):
    fake = MagicMock()
    fake.clone_tts.return_value = b"RIFF" + b"\x00" * 100
    out = tmp_path / "out.wav"
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["clone", "say", "alice", "hello", "--out", str(out)])
    assert r.exit_code == 0
    fake.clone_tts.assert_called_once_with("alice", "hello", language="English")


def test_record_with_audio_and_text_skips_recorder(runner, tmp_path):
    """`mimic record alice --audio ref.wav --text "..."` skips the interactive flow."""
    audio = tmp_path / "ref.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)

    fake = MagicMock()
    fake.clone_register.return_value = {"status": "ok", "name": "alice"}

    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, [
            "record", "alice",
            "--audio", str(audio),
            "--text", "transcript here",
        ])
    assert r.exit_code == 0
    fake.clone_register.assert_called_once()


def test_config_prints_resolved_settings(runner, tmp_path):
    (tmp_path / "config.toml").write_text(
        'server_url = "http://nas.local:8000"\ndefault_voice = "Aiden"\n'
    )
    r = runner.invoke(app, ["config"])
    assert r.exit_code == 0
    assert "nas.local" in r.stdout
    assert "Aiden" in r.stdout
```

- [ ] **Step 2: Run the test (should fail — no module yet)**

```bash
.venv/bin/pytest client/tests/test_cli.py -v
```
Expected: ImportError on `mimic.cli`.

- [ ] **Step 3: Update `client/mimic/config.py` to honor `MIMIC_CONFIG_DIR` for testing**

Edit the `_config_dir()` function to check the env var first:

```python
def _config_dir() -> Path:
    override = os.environ.get("MIMIC_CONFIG_DIR")
    if override:
        return Path(override)
    return user_config_path("mimic", appauthor=False)
```

- [ ] **Step 4: Implement `client/mimic/cli.py`**

```python
"""`mimic` CLI — typer-based command-line interface."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Annotated

import typer

from mimic.client import Client
from mimic.config import load_config
from mimic.recorder import (
    DEFAULT_SAMPLE_RATE,
    pick_script,
    play,
    record_until_enter,
    save_wav,
)

app = typer.Typer(no_args_is_help=True, add_completion=False, help="mimic-tts CLI")
clone_app = typer.Typer(no_args_is_help=True, help="Clone voice operations")
app.add_typer(clone_app, name="clone")


def _client() -> Client:
    cfg = load_config()
    return Client(server_url=cfg.server_url, token=cfg.token)


@app.command()
def say(
    text: Annotated[str, typer.Argument(help="Text to synthesize.")],
    voice: Annotated[str | None, typer.Option(help="Speaker name.")] = None,
    out: Annotated[Path, typer.Option(help="Output wav path.")] = Path("out.wav"),
    language: Annotated[str, typer.Option()] = "English",
) -> None:
    """Synthesize speech with a built-in voice."""
    cfg = load_config()
    speaker = voice or cfg.default_voice
    with _client() as c:
        c.tts_to_file(text, out, speaker=speaker, language=language)
    typer.echo(f"wrote {out}")


@app.command()
def voices() -> None:
    """List built-in voices."""
    with _client() as c:
        for v in c.list_voices():
            typer.echo(f"{v['name']:12s} {v['language']}")


@app.command()
def clones() -> None:
    """List registered clone voices."""
    with _client() as c:
        for name in c.list_clones():
            typer.echo(name)


@app.command()
def health() -> None:
    """Show server health and currently loaded models."""
    with _client() as c:
        info = c.health()
    typer.echo(info)


@app.command(name="config")
def show_config() -> None:
    """Print the resolved client configuration."""
    cfg = load_config()
    typer.echo(f"server_url    {cfg.server_url}")
    typer.echo(f"token         {'<set>' if cfg.token else '<none>'}")
    typer.echo(f"default_voice {cfg.default_voice}")


@app.command()
def record(
    name: Annotated[str, typer.Argument(help="Name to register the clone under.")],
    audio: Annotated[Path | None, typer.Option(help="Skip the recorder; use this file.")] = None,
    text: Annotated[str | None, typer.Option(help="Transcript for --audio.")] = None,
) -> None:
    """Record a reference voice and register it on the server."""
    if audio is not None:
        if text is None:
            typer.echo("--text is required when --audio is provided", err=True)
            raise typer.Exit(2)
        with _client() as c:
            result = c.clone_register(name, audio, text)
        typer.echo(f"registered '{result['name']}'")
        return

    _interactive_record_and_register(name)


@clone_app.command(name="say")
def clone_say(
    name: Annotated[str, typer.Argument(help="Registered clone name.")],
    text: Annotated[str, typer.Argument()],
    out: Annotated[Path, typer.Option(help="Output wav path.")] = Path("out.wav"),
    language: Annotated[str, typer.Option()] = "English",
) -> None:
    """Synthesize speech using a registered clone voice."""
    with _client() as c:
        audio = c.clone_tts(name, text, language=language)
    out.write_bytes(audio)
    typer.echo(f"wrote {out}")


def _interactive_record_and_register(name: str) -> None:
    """Drive the guided recorder. Kept thin; primitives live in `mimic.recorder`."""
    script = pick_script()
    typer.echo(f"\nRead this script when ready:\n\n  {script}\n")
    typer.prompt("Press Enter to start recording", default="", show_default=False)

    typer.echo("Recording… press Enter to stop.")
    stop = threading.Event()
    waiter = threading.Thread(target=lambda: (sys.stdin.readline(), stop.set()))
    waiter.daemon = True
    waiter.start()

    result = record_until_enter(
        sample_rate=DEFAULT_SAMPLE_RATE, channels=1,
        max_seconds=30.0, stop_event=stop,
    )

    typer.echo("Playing back…")
    play(result.audio, result.sample_rate)

    keep = typer.prompt("Keep this take? [y/N/r=retry]", default="N").strip().lower()
    if keep == "r":
        return _interactive_record_and_register(name)
    if not keep.startswith("y"):
        typer.echo("discarded.")
        raise typer.Exit(0)

    transcript = typer.prompt("Transcript", default=script)

    import io
    buf = io.BytesIO()
    save_wav(buf, result.audio, sample_rate=result.sample_rate)
    buf.seek(0)

    with _client() as c:
        out = c.clone_register(name, buf.read(), transcript)
    typer.echo(f"registered '{out['name']}'")
```

- [ ] **Step 5: Run the tests until green**

```bash
.venv/bin/pytest client/tests/test_cli.py -v
```
Expected: 8 passed.

- [ ] **Step 6: Run the full client test suite**

```bash
.venv/bin/pytest client/tests/ -v
```
Expected: all client tests pass.

- [ ] **Step 7: Smoke the CLI**

```bash
.venv/bin/mimic --help
```
Expected: typer prints the command list including `say`, `record`, `clone`, `voices`, `clones`, `config`, `health`.

- [ ] **Step 8: Commit**

```bash
git add client/mimic/cli.py client/mimic/config.py client/tests/test_cli.py
git commit -m "feat(client): add mimic CLI (say, record, clone say, voices, clones, config, health)"
```

---

## End-of-plan verification

- [ ] All client tests pass: `.venv/bin/pytest client/tests/ -v`
- [ ] All server tests still pass: `.venv/bin/pytest server/tests/ -v`
- [ ] Lint clean: `.venv/bin/ruff check client/ server/ && .venv/bin/ruff format --check client/ server/`
- [ ] `mimic --help` works
- [ ] `mimic config` prints resolved config (defaults, no error)

## Manual end-to-end smoke (requires running server)

With the server running (Plan 1 Docker) and `MIMIC_SERVER_URL` set:

```bash
mimic health
mimic voices
mimic say "hello world" --out hello.wav
# play hello.wav
mimic record alice --audio some_sample.wav --text "the transcript"
mimic clone say alice "this is alice cloned"
```

## State after this plan

- `client/` package fully implemented: `Client`, `AsyncClient`, config resolution, recorder, CLI.
- 41+ client tests passing (10 base + 13 sync + 7 async + 8 config + 5 recorder + 8 CLI).
- `mimic` console command works locally via `uv run mimic` or `.venv/bin/mimic`.
- Plan 3 covers: `lint.sh`, `release.sh`, CI workflows (PyPI OIDC + GHCR), README rewrite, `docs/*.md`, `NOTICE`.
