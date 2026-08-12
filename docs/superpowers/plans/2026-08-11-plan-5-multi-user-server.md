# Multi-User Auth — Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single shared bearer token with per-user API keys that own voices, so friends can use the server without gaining access to the owner's cloned voice.

**Architecture:** A SQLite DB in the data volume becomes the ownership index; reference audio stays on disk under `reference/<owner-label>/<voice>/`. A `current_caller` FastAPI dependency resolves every request to a `Caller` (an API key row). Domain modules (`identity`, `voices`, `usage`) hold the rules and raise typed errors; a thin `routes/` package maps them to HTTP.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, stdlib `sqlite3`, pytest.

## Global Constraints

- Python `>=3.12,<3.13`. Ruff line-length 100, `target-version = py312`, mccabe max-complexity 10.
- Ruff rule `DTZ` is on: every timestamp must be timezone-aware. Use `datetime.now(UTC).isoformat()`, never `utcnow()`.
- All new modules start with `from __future__ import annotations`.
- Never `--no-verify`. Run `./lint.sh` before every commit; it runs ruff format, ruff check --fix, mypy, and both pytest suites.
- Tests use the existing style: `TestClient`, a `MagicMock` backend, `tmp_path` for `reference_dir`.
- **Hard invariant:** no endpoint ever returns the bytes of `audio.wav` or the contents of `text.txt` to any caller, including admin.
- Timestamps are stored as ISO-8601 UTC strings.
- Token format is `mk_<43 chars base64url>`; SHA-256 hashed at rest; the first 8 characters after the prefix are stored in the clear as `token_prefix`.
- Comments follow `CLAUDE.md`: prefer expressive naming over commentary; a comment must explain *why*, never restate the code.

---

### Task 1: Split `app.py` into a `routes/` package

Pure refactor. No behavior change; the existing `server/tests/test_app.py` is the safety net and must stay green untouched.

**Files:**
- Create: `server/mimic_server/services.py`
- Create: `server/mimic_server/routes/__init__.py`
- Create: `server/mimic_server/routes/tts.py`
- Create: `server/mimic_server/routes/clones.py`
- Create: `server/mimic_server/routes/openai.py`
- Create: `server/mimic_server/routes/system.py`
- Create: `server/mimic_server/audio.py`
- Modify: `server/mimic_server/app.py` (becomes wiring only)
- Test: `server/tests/test_app.py` (unchanged — it must pass as-is)

**Interfaces:**
- Consumes: nothing.
- Produces: `Services` dataclass; `routes.<module>.register(app: FastAPI, svc: Services) -> None`; `audio.wav_response`, `audio.audio_response`, `audio.transcode_to_wav`.

- [ ] **Step 1: Move the audio helpers into `audio.py`**

Cut `_wav_response`, `_audio_response`, and `_transcode_to_wav` out of `app.py` verbatim into a new `server/mimic_server/audio.py`, renaming them to `wav_response`, `audio_response`, `transcode_to_wav` (drop the leading underscore — they are now public across modules). Keep their bodies, imports (`io`, `soundfile as sf`, `Response`, `StreamingResponse`, etc.), and docstrings exactly as they are.

- [ ] **Step 2: Create the `Services` container**

```python
# server/mimic_server/services.py
"""Everything a route module needs, assembled once at app construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mimic_server.backends.base import TTSBackend
    from mimic_server.config import Settings


@dataclass
class Services:
    settings: Settings
    backend: TTSBackend
    auth: Any  # fastapi.Depends(...) marker; replaced by `caller` in Task 4
```

- [ ] **Step 3: Move routes into their modules**

Each module exposes `register(app, svc)` and contains the handlers moved verbatim from `build_app`:

- `routes/system.py` — `/health`, `/voices`, `/stt`
- `routes/tts.py` — `/tts`
- `routes/clones.py` — `/clone/voices`, `/clone/register`, `DELETE /clone/voices/{name}`, `/clone/tts`, `/clone/oneshot`
- `routes/openai.py` — `/v1/audio/speech`, plus `_OpenAISpeechRequest`, `_OPENAI_FORMATS`, and `_handle_openai_speech`

Shape of each module:

```python
# server/mimic_server/routes/tts.py
"""Built-in-voice synthesis route."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Form

from mimic_server.audio import audio_response

if TYPE_CHECKING:
    from fastapi import FastAPI

    from mimic_server.services import Services


def register(app: FastAPI, svc: Services) -> None:
    @app.post("/tts", dependencies=[svc.auth])
    async def tts(
        text: Annotated[str, Form()],
        language: Annotated[str, Form()] = "English",
        speaker: Annotated[str, Form()] = "default",
        instruct: Annotated[str, Form()] = "",
        fmt: Annotated[str, Form(alias="format")] = "wav",
    ):
        samples, sr = svc.backend.synth_builtin(
            text=text, speaker=speaker, language=language, instruct=instruct or None
        )
        return audio_response(samples, sr, fmt=fmt)
```

`_resolve_clone` moves to `routes/clones.py` for now; Task 8 deletes it.

- [ ] **Step 4: Reduce `app.py` to wiring**

```python
def build_app(
    settings: Settings,
    backend_factory: Callable[[Settings], TTSBackend] | None = None,
) -> FastAPI:
    _configure_environment(settings)
    backend = (backend_factory or make_backend)(settings)
    svc = Services(settings=settings, backend=backend, auth=Depends(require_token(settings)))
    app = FastAPI(title="mimic-tts API", lifespan=_make_lifespan(backend, settings))
    for module in (system, tts, clones, openai):
        module.register(app, svc)
    _mount_web_ui(app)
    return app
```

`_configure_environment`, `_check_public_bind_auth`, `_make_lifespan`, and `_mount_web_ui` stay in `app.py`. Drop the `# noqa: C901` — `build_app` is no longer complex.

- [ ] **Step 5: Verify the existing suite still passes unchanged**

Run: `./lint.sh`
Expected: all green. If `test_app.py` needed *any* edit, the refactor changed behavior — revert that part.

- [ ] **Step 6: Commit**

```bash
git add server/mimic_server
git commit -m "refactor(server): split app.py into a routes package"
```

---

### Task 2: SQLite database layer

**Files:**
- Create: `server/mimic_server/db.py`
- Test: `server/tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Database(path: Path)` with `.cursor()` context manager, `.migrate() -> None`, `.close() -> None`; module constant `MIGRATIONS: list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_db.py
from mimic_server.db import Database


def test_migrate_creates_tables(tmp_path):
    db = Database(tmp_path / "mimic.db")
    db.migrate()
    with db.cursor() as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row["name"] for row in cur.fetchall()}
    assert {"api_keys", "voices", "voice_grants", "usage_events", "schema_version"} <= tables


def test_migrate_is_idempotent(tmp_path):
    db = Database(tmp_path / "mimic.db")
    db.migrate()
    db.migrate()
    with db.cursor() as cur:
        cur.execute("SELECT version FROM schema_version")
        assert cur.fetchone()["version"] == len(__import__("mimic_server.db", fromlist=["x"]).MIGRATIONS)


def test_cursor_commits_on_success(tmp_path):
    path = tmp_path / "mimic.db"
    db = Database(path)
    db.migrate()
    with db.cursor() as cur:
        cur.execute("INSERT INTO schema_version (version) VALUES (99)")
    reopened = Database(path)
    with reopened.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM schema_version WHERE version = 99")
        assert cur.fetchone()["n"] == 1


def test_cursor_rolls_back_on_error(tmp_path):
    db = Database(tmp_path / "mimic.db")
    db.migrate()
    try:
        with db.cursor() as cur:
            cur.execute("INSERT INTO schema_version (version) VALUES (99)")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM schema_version WHERE version = 99")
        assert cur.fetchone()["n"] == 0


def test_foreign_keys_enforced(tmp_path):
    import sqlite3

    import pytest

    db = Database(tmp_path / "mimic.db")
    db.migrate()
    with pytest.raises(sqlite3.IntegrityError), db.cursor() as cur:
        cur.execute("INSERT INTO voices (owner_key_id, name, visibility, created_at) VALUES (999, 'x', 'private', 'now')")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mimic_server.db'`

- [ ] **Step 3: Implement `db.py`**

```python
"""SQLite storage for keys, voice ownership, and usage.

Connections are per-thread because FastAPI runs sync route handlers in a
threadpool and SQLite connection objects are not shareable across threads.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

MIGRATIONS: list[str] = [
    """
    CREATE TABLE api_keys (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        label           TEXT    NOT NULL UNIQUE,
        token_hash      TEXT    NOT NULL,
        token_prefix    TEXT    NOT NULL,
        role            TEXT    NOT NULL DEFAULT 'user',
        enabled         INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT    NOT NULL,
        last_used_at    TEXT,
        expires_at      TEXT,
        can_upload      INTEGER NOT NULL DEFAULT 1,
        max_voices      INTEGER NOT NULL DEFAULT 5,
        daily_char_quota INTEGER NOT NULL DEFAULT 50000,
        managed_by_env  INTEGER NOT NULL DEFAULT 0,
        notes           TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_api_keys_prefix ON api_keys (token_prefix);

    CREATE TABLE voices (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_key_id INTEGER NOT NULL REFERENCES api_keys (id) ON DELETE CASCADE,
        name         TEXT    NOT NULL,
        visibility   TEXT    NOT NULL DEFAULT 'private',
        created_at   TEXT    NOT NULL,
        UNIQUE (owner_key_id, name)
    );

    CREATE TABLE voice_grants (
        voice_id        INTEGER NOT NULL REFERENCES voices (id) ON DELETE CASCADE,
        grantee_key_id  INTEGER NOT NULL REFERENCES api_keys (id) ON DELETE CASCADE,
        granted_by      INTEGER NOT NULL,
        created_at      TEXT    NOT NULL,
        PRIMARY KEY (voice_id, grantee_key_id)
    );

    CREATE TABLE usage_events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        key_id        INTEGER NOT NULL,
        ts            TEXT    NOT NULL,
        endpoint      TEXT    NOT NULL,
        voice_id      INTEGER,
        chars         INTEGER NOT NULL DEFAULT 0,
        audio_seconds REAL    NOT NULL DEFAULT 0,
        status        INTEGER NOT NULL DEFAULT 200
    );
    CREATE INDEX idx_usage_key_ts ON usage_events (key_id, ts);
    """,
]


class Database:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._local = threading.local()

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self._connection()
        cur = conn.cursor()
        cur.execute("BEGIN")
        try:
            yield cur
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            cur.close()

    def migrate(self) -> None:
        conn = self._connection()
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] or 0
        for index, script in enumerate(MIGRATIONS[current:], start=current + 1):
            with self.cursor() as cur:
                cur.executescript(script)
                cur.execute("INSERT INTO schema_version (version) VALUES (?)", (index,))

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest server/tests/test_db.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add server/mimic_server/db.py server/tests/test_db.py
git commit -m "feat(server): add SQLite storage layer with migrations"
```

---

### Task 3: Typed domain errors

**Files:**
- Create: `server/mimic_server/errors.py`
- Test: `server/tests/test_errors.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MimicError(message, *, extra=None)` with class attributes `status: int` and `code: str`; subclasses `Unauthorized`, `Forbidden`, `VoiceNotFound`, `AmbiguousVoice`, `VoiceLimitReached`, `QuotaExceeded`, `UploadNotAllowed`, `LabelInUse`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_errors.py
from mimic_server.errors import AmbiguousVoice, MimicError, QuotaExceeded, VoiceNotFound


def test_error_carries_status_and_code():
    err = VoiceNotFound("no voice 'x'")
    assert err.status == 404
    assert err.code == "voice_not_found"
    assert str(err) == "no voice 'x'"


def test_error_payload_merges_extra():
    err = QuotaExceeded("over", extra={"used": 10, "limit": 5})
    assert err.status == 429
    assert err.payload() == {
        "error": "quota_exceeded",
        "detail": "over",
        "used": 10,
        "limit": 5,
    }


def test_ambiguous_is_409_and_a_mimic_error():
    err = AmbiguousVoice("pick one", extra={"candidates": ["a/x", "b/x"]})
    assert err.status == 409
    assert isinstance(err, MimicError)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Domain errors. Route handlers raise these; one FastAPI exception handler
maps them to responses, so HTTP status choices live in exactly one place."""

from __future__ import annotations

from typing import Any


class MimicError(Exception):
    status: int = 400
    code: str = "error"

    def __init__(self, message: str, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra or {}

    def payload(self) -> dict[str, Any]:
        return {"error": self.code, "detail": self.message, **self.extra}


class Unauthorized(MimicError):
    status = 401
    code = "unauthorized"


class Forbidden(MimicError):
    status = 403
    code = "forbidden"


class UploadNotAllowed(Forbidden):
    code = "upload_not_allowed"


class VoiceNotFound(MimicError):
    status = 404
    code = "voice_not_found"


class AmbiguousVoice(MimicError):
    status = 409
    code = "ambiguous_voice"


class VoiceLimitReached(MimicError):
    status = 409
    code = "voice_limit_reached"


class LabelInUse(MimicError):
    status = 409
    code = "label_in_use"


class QuotaExceeded(MimicError):
    status = 429
    code = "quota_exceeded"
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest server/tests/test_errors.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add server/mimic_server/errors.py server/tests/test_errors.py
git commit -m "feat(server): add typed domain errors"
```

---

### Task 4: Key model and store

**Files:**
- Create: `server/mimic_server/identity.py`
- Test: `server/tests/test_identity.py`

**Interfaces:**
- Consumes: `Database` (Task 2), `LabelInUse` (Task 3).
- Produces:
  - `Key` frozen dataclass: `id, label, token_prefix, role, enabled, created_at, last_used_at, expires_at, can_upload, max_voices, daily_char_quota, managed_by_env, notes`; property `is_admin -> bool`.
  - `Caller` frozen dataclass wrapping `key: Key`; properties `id`, `label`, `is_admin`.
  - `generate_token() -> str`, `hash_token(token: str) -> str`, `prefix_of(token: str) -> str`.
  - `KeyStore(db)` with `create(label, *, role="user", can_upload=True, max_voices=5, daily_char_quota=50000, expires_at=None, notes="", managed_by_env=False, token=None) -> tuple[Key, str]`, `get_by_label(label) -> Key | None`, `get_by_id(key_id) -> Key | None`, `authenticate(token) -> Key | None`, `list_all() -> list[Key]`, `update(label, **fields) -> Key`, `touch(key_id) -> None`, `delete(label) -> None`, `ensure_env_root(token, label) -> Key`.
  - Constants `DEFAULT_MAX_VOICES = 5`, `DEFAULT_DAILY_CHAR_QUOTA = 50000`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_identity.py
import pytest
from mimic_server.db import Database
from mimic_server.errors import LabelInUse
from mimic_server.identity import KeyStore, generate_token, hash_token, prefix_of


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "mimic.db")
    db.migrate()
    return KeyStore(db)


def test_generated_tokens_are_prefixed_and_unique():
    a, b = generate_token(), generate_token()
    assert a.startswith("mk_") and b.startswith("mk_")
    assert a != b
    assert prefix_of(a) == a[3:11]


def test_create_returns_key_and_plaintext_once(store):
    key, token = store.create("dave")
    assert key.label == "dave"
    assert key.role == "user"
    assert key.enabled is True
    assert key.can_upload is True
    assert key.max_voices == 5
    assert key.daily_char_quota == 50000
    assert key.token_prefix == prefix_of(token)


def test_stored_hash_is_not_the_token(store):
    _, token = store.create("dave")
    with store.db.cursor() as cur:
        cur.execute("SELECT token_hash FROM api_keys WHERE label = 'dave'")
        stored = cur.fetchone()["token_hash"]
    assert stored != token
    assert stored == hash_token(token)


def test_duplicate_label_rejected(store):
    store.create("dave")
    with pytest.raises(LabelInUse):
        store.create("dave")


def test_authenticate_round_trip(store):
    _, token = store.create("dave")
    assert store.authenticate(token).label == "dave"


def test_authenticate_rejects_unknown_disabled_and_expired(store):
    assert store.authenticate("mk_nope") is None

    _, token = store.create("dave")
    store.update("dave", enabled=False)
    assert store.authenticate(token) is None

    _, token2 = store.create("erin", expires_at="2000-01-01T00:00:00+00:00")
    assert store.authenticate(token2) is None


def test_authenticate_rejects_right_prefix_wrong_secret(store):
    _, token = store.create("dave")
    forged = token[:11] + ("x" * (len(token) - 11))
    assert store.authenticate(forged) is None


def test_update_changes_quotas(store):
    store.create("dave")
    updated = store.update("dave", max_voices=1, daily_char_quota=10, can_upload=False)
    assert (updated.max_voices, updated.daily_char_quota, updated.can_upload) == (1, 10, False)


def test_touch_sets_last_used(store):
    key, _ = store.create("dave")
    assert key.last_used_at is None
    store.touch(key.id)
    assert store.get_by_label("dave").last_used_at is not None


def test_ensure_env_root_is_idempotent_and_rotates(store):
    root = store.ensure_env_root("secret-one", "root")
    assert root.is_admin and root.managed_by_env is True
    again = store.ensure_env_root("secret-two", "root")
    assert again.id == root.id
    assert store.authenticate("secret-one") is None
    assert store.authenticate("secret-two").id == root.id
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_identity.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Key points for the implementer:

- `authenticate` looks the row up by `token_prefix` (indexed) and then compares hashes with `secrets.compare_digest`. Never compare plaintext tokens, and never query by hash directly — the prefix index keeps the lookup cheap while the digest comparison stays constant-time.
- `ensure_env_root` writes the hash of the *current* env token every boot, which is what makes rotating `MIMIC_API_TOKEN` work. It must also force `role='admin'`, `enabled=1`, `managed_by_env=1`.
- Expiry comparison is string comparison of ISO-8601 UTC, which is correct because the format is lexicographically ordered.

```python
"""API-key identity: token generation, hashing, and the key store."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mimic_server.errors import LabelInUse

if TYPE_CHECKING:
    from mimic_server.db import Database

TOKEN_PREFIX = "mk_"
PREFIX_LENGTH = 8
DEFAULT_MAX_VOICES = 5
DEFAULT_DAILY_CHAR_QUOTA = 50000

_UPDATABLE = frozenset(
    {"enabled", "can_upload", "max_voices", "daily_char_quota", "expires_at", "notes", "role"}
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def prefix_of(token: str) -> str:
    return token.removeprefix(TOKEN_PREFIX)[:PREFIX_LENGTH]


@dataclass(frozen=True)
class Key:
    id: int
    label: str
    token_prefix: str
    role: str
    enabled: bool
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    can_upload: bool
    max_voices: int
    daily_char_quota: int
    managed_by_env: bool
    notes: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= now_iso()


@dataclass(frozen=True)
class Caller:
    """The authenticated identity behind a request."""

    key: Key

    @property
    def id(self) -> int:
        return self.key.id

    @property
    def label(self) -> str:
        return self.key.label

    @property
    def is_admin(self) -> bool:
        return self.key.is_admin


def _row_to_key(row: sqlite3.Row) -> Key:
    return Key(
        id=row["id"],
        label=row["label"],
        token_prefix=row["token_prefix"],
        role=row["role"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        expires_at=row["expires_at"],
        can_upload=bool(row["can_upload"]),
        max_voices=row["max_voices"],
        daily_char_quota=row["daily_char_quota"],
        managed_by_env=bool(row["managed_by_env"]),
        notes=row["notes"],
    )


class KeyStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        label: str,
        *,
        role: str = "user",
        can_upload: bool = True,
        max_voices: int = DEFAULT_MAX_VOICES,
        daily_char_quota: int = DEFAULT_DAILY_CHAR_QUOTA,
        expires_at: str | None = None,
        notes: str = "",
        managed_by_env: bool = False,
        token: str | None = None,
    ) -> tuple[Key, str]:
        plaintext = token or generate_token()
        try:
            with self.db.cursor() as cur:
                cur.execute(
                    """INSERT INTO api_keys
                       (label, token_hash, token_prefix, role, enabled, created_at,
                        expires_at, can_upload, max_voices, daily_char_quota,
                        managed_by_env, notes)
                       VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        label,
                        hash_token(plaintext),
                        prefix_of(plaintext),
                        role,
                        now_iso(),
                        expires_at,
                        int(can_upload),
                        max_voices,
                        daily_char_quota,
                        int(managed_by_env),
                        notes,
                    ),
                )
        except sqlite3.IntegrityError as e:
            raise LabelInUse(f"a key labeled {label!r} already exists") from e
        created = self.get_by_label(label)
        assert created is not None
        return created, plaintext

    def get_by_label(self, label: str) -> Key | None:
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM api_keys WHERE label = ?", (label,))
            row = cur.fetchone()
        return _row_to_key(row) if row else None

    def get_by_id(self, key_id: int) -> Key | None:
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,))
            row = cur.fetchone()
        return _row_to_key(row) if row else None

    def authenticate(self, token: str) -> Key | None:
        expected = hash_token(token)
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM api_keys WHERE token_prefix = ?", (prefix_of(token),))
            rows = cur.fetchall()
        for row in rows:
            if secrets.compare_digest(row["token_hash"], expected):
                key = _row_to_key(row)
                return None if not key.enabled or key.is_expired else key
        return None

    def list_all(self) -> list[Key]:
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM api_keys ORDER BY label")
            return [_row_to_key(r) for r in cur.fetchall()]

    def update(self, label: str, **fields: Any) -> Key:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        with self.db.cursor() as cur:
            cur.execute(f"UPDATE api_keys SET {assignments} WHERE label = ?", [*values, label])  # noqa: S608
        updated = self.get_by_label(label)
        if updated is None:
            raise KeyError(label)
        return updated

    def touch(self, key_id: int) -> None:
        with self.db.cursor() as cur:
            cur.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now_iso(), key_id))

    def delete(self, label: str) -> None:
        with self.db.cursor() as cur:
            cur.execute("DELETE FROM api_keys WHERE label = ?", (label,))

    def ensure_env_root(self, token: str, label: str) -> Key:
        """Seed or refresh the env-managed root admin key.

        Rewriting the hash on every boot is what makes rotating
        MIMIC_API_TOKEN take effect without a manual migration.
        """
        existing = self.get_by_label(label)
        if existing is None:
            key, _ = self.create(
                label,
                role="admin",
                token=token,
                managed_by_env=True,
                notes="root key, managed by MIMIC_API_TOKEN",
            )
            return key
        with self.db.cursor() as cur:
            cur.execute(
                """UPDATE api_keys
                   SET token_hash = ?, token_prefix = ?, role = 'admin',
                       enabled = 1, managed_by_env = 1, expires_at = NULL
                   WHERE id = ?""",
                (hash_token(token), prefix_of(token), existing.id),
            )
        refreshed = self.get_by_label(label)
        assert refreshed is not None
        return refreshed
```

The `# noqa: S608` on the UPDATE is justified: column names come from the `_UPDATABLE` allowlist, never from user input, and the values are bound parameters.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest server/tests/test_identity.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add server/mimic_server/identity.py server/tests/test_identity.py
git commit -m "feat(server): add API key model and store"
```

---

### Task 5: Voice registry — ownership, visibility, grants, resolution

**Files:**
- Create: `server/mimic_server/voices.py`
- Test: `server/tests/test_voices.py`

**Interfaces:**
- Consumes: `Database`, `KeyStore`, `Caller`, and the errors from Task 3.
- Produces:
  - `Voice` frozen dataclass: `id, owner_id, owner_label, name, visibility, created_at`; property `qualified -> str` returning `"<owner_label>/<name>"`.
  - `VoiceRegistry(db, keys, reference_dir)` with `register(caller, name, wav_bytes, ref_text) -> Voice`, `resolve(caller, spec) -> Voice`, `visible_to(caller) -> list[Voice]`, `all_voices() -> list[Voice]`, `delete(caller, spec) -> Voice`, `set_visibility(caller, spec, visibility) -> Voice`, `grant(caller, spec, grantee_label) -> None`, `revoke_grant(caller, spec, grantee_label) -> None`, `grants_for(voice) -> list[str]`, `count_owned(key_id) -> int`, `reference_paths(voice) -> tuple[Path, str]`, `dir_for(owner_label, name) -> Path`, `adopt(owner, name) -> Voice`.
  - `VALID_NAME` regex and `validate_name(name) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_voices.py
import pytest
from mimic_server.db import Database
from mimic_server.errors import (
    AmbiguousVoice,
    Forbidden,
    UploadNotAllowed,
    VoiceLimitReached,
    VoiceNotFound,
)
from mimic_server.identity import Caller, KeyStore
from mimic_server.voices import VoiceRegistry


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "mimic.db")
    db.migrate()
    keys = KeyStore(db)
    registry = VoiceRegistry(db, keys, tmp_path / "reference")
    admin, _ = keys.create("root", role="admin")
    dave, _ = keys.create("dave")
    erin, _ = keys.create("erin")
    return registry, Caller(admin), Caller(dave), Caller(erin)


def _register(registry, caller, name):
    return registry.register(caller, name, b"RIFFfake", f"transcript for {name}")


def test_register_writes_audio_under_owner_namespace(env):
    registry, _, dave, _ = env
    voice = _register(registry, dave, "warm")
    assert voice.qualified == "dave/warm"
    assert (registry.dir_for("dave", "warm") / "audio.wav").read_bytes() == b"RIFFfake"
    assert registry.reference_paths(voice)[1] == "transcript for warm"


def test_same_name_different_owners_coexist(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    _register(registry, erin, "warm")
    assert registry.resolve(dave, "warm").owner_label == "dave"
    assert registry.resolve(erin, "warm").owner_label == "erin"


def test_private_voice_is_invisible_to_others(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    assert registry.visible_to(erin) == []
    with pytest.raises(VoiceNotFound):
        registry.resolve(erin, "dave/warm")


def test_admin_sees_and_resolves_every_voice(env):
    registry, admin, dave, _ = env
    _register(registry, dave, "warm")
    assert registry.resolve(admin, "dave/warm").owner_label == "dave"
    assert [v.qualified for v in registry.all_voices()] == ["dave/warm"]


def test_public_voice_is_resolvable_by_anyone(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    registry.set_visibility(dave, "warm", "public")
    assert registry.resolve(erin, "dave/warm").qualified == "dave/warm"


def test_grant_makes_a_private_voice_usable(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    registry.grant(dave, "warm", "erin")
    assert registry.resolve(erin, "dave/warm").qualified == "dave/warm"
    assert registry.grants_for(registry.resolve(dave, "warm")) == ["erin"]
    registry.revoke_grant(dave, "warm", "erin")
    with pytest.raises(VoiceNotFound):
        registry.resolve(erin, "dave/warm")


def test_admin_can_grant_someone_elses_voice(env):
    registry, admin, dave, erin = env
    _register(registry, dave, "warm")
    registry.grant(admin, "dave/warm", "erin")
    assert registry.resolve(erin, "dave/warm").qualified == "dave/warm"


def test_non_owner_cannot_grant_or_delete(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    registry.set_visibility(dave, "warm", "public")
    with pytest.raises(Forbidden):
        registry.grant(erin, "dave/warm", "erin")
    with pytest.raises(Forbidden):
        registry.delete(erin, "dave/warm")


def test_bare_name_prefers_own_voice_over_a_public_one(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    registry.set_visibility(dave, "warm", "public")
    _register(registry, erin, "warm")
    assert registry.resolve(erin, "warm").owner_label == "erin"


def test_bare_name_is_ambiguous_across_two_public_voices(env):
    registry, admin, dave, erin = env
    _register(registry, dave, "warm")
    _register(registry, erin, "warm")
    registry.set_visibility(dave, "warm", "public")
    registry.set_visibility(erin, "warm", "public")
    with pytest.raises(AmbiguousVoice) as exc:
        registry.resolve(admin, "warm")
    assert exc.value.extra["candidates"] == ["dave/warm", "erin/warm"]


def test_delete_removes_row_and_files(env):
    registry, _, dave, _ = env
    voice = _register(registry, dave, "warm")
    path = registry.dir_for("dave", "warm")
    registry.delete(dave, "warm")
    assert not path.exists()
    assert registry.visible_to(dave) == []
    with pytest.raises(VoiceNotFound):
        registry.resolve(dave, "warm")


def test_re_registering_same_name_replaces_in_place(env):
    registry, _, dave, _ = env
    first = _register(registry, dave, "warm")
    registry.register(dave, "warm", b"RIFFnew", "new transcript")
    assert registry.count_owned(dave.id) == 1
    assert registry.resolve(dave, "warm").id == first.id
    assert (registry.dir_for("dave", "warm") / "audio.wav").read_bytes() == b"RIFFnew"


def test_max_voices_enforced(env):
    registry, _, dave, _ = env
    registry.keys.update("dave", max_voices=1)
    _register(registry, dave, "one")
    with pytest.raises(VoiceLimitReached):
        _register(registry, dave, "two")


def test_upload_forbidden_when_can_upload_false(env):
    registry, _, dave, _ = env
    registry.keys.update("dave", can_upload=False)
    with pytest.raises(UploadNotAllowed):
        _register(registry, dave, "warm")


@pytest.mark.parametrize("bad", ["../evil", "a/b", "", ".", "..", "has space", "x" * 65])
def test_invalid_names_rejected(env, bad):
    registry, _, dave, _ = env
    with pytest.raises(VoiceNotFound if "/" in bad else ValueError):
        registry.register(dave, bad, b"RIFF", "t")
```

The last test's expectation deserves a note: a name containing `/` is parsed as a qualified spec on register and rejected as a bad owner, everything else fails name validation. If that split feels muddy when implementing, simplify by making `register` reject any name containing `/` with `ValueError` and change the test's `pytest.raises` to plain `ValueError`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_voices.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Resolution order, which is the heart of this module:

1. Split `spec` on `/`. Qualified (`owner/name`) → look up that exact row, then apply the visibility check.
2. Bare → the caller's own voice wins if it exists.
3. Otherwise gather every *other* visible voice with that name; exactly one → return it; zero → `VoiceNotFound`; more → `AmbiguousVoice` with sorted qualified candidates.

Built-in backend voices are *not* this module's concern — route handlers check built-ins before calling `resolve`.

```python
"""Voice ownership, visibility, grants, and name resolution."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mimic_server.errors import (
    AmbiguousVoice,
    Forbidden,
    UploadNotAllowed,
    VoiceLimitReached,
    VoiceNotFound,
)
from mimic_server.identity import now_iso

if TYPE_CHECKING:
    import sqlite3

    from mimic_server.db import Database
    from mimic_server.identity import Caller, Key, KeyStore

VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VISIBILITIES = frozenset({"private", "public"})


def validate_name(name: str) -> None:
    if not VALID_NAME.match(name):
        raise ValueError(
            f"invalid voice name {name!r}: use 1-64 chars of letters, digits, dot, dash, underscore"
        )


@dataclass(frozen=True)
class Voice:
    id: int
    owner_id: int
    owner_label: str
    name: str
    visibility: str
    created_at: str

    @property
    def qualified(self) -> str:
        return f"{self.owner_label}/{self.name}"


def _row_to_voice(row: sqlite3.Row) -> Voice:
    return Voice(
        id=row["id"],
        owner_id=row["owner_key_id"],
        owner_label=row["owner_label"],
        name=row["name"],
        visibility=row["visibility"],
        created_at=row["created_at"],
    )


_SELECT = """
    SELECT v.*, k.label AS owner_label
    FROM voices v JOIN api_keys k ON k.id = v.owner_key_id
"""


class VoiceRegistry:
    def __init__(self, db: Database, keys: KeyStore, reference_dir: Path) -> None:
        self.db = db
        self.keys = keys
        self.reference_dir = Path(reference_dir)

    def dir_for(self, owner_label: str, name: str) -> Path:
        return self.reference_dir / owner_label / name

    def reference_paths(self, voice: Voice) -> tuple[Path, str]:
        base = self.dir_for(voice.owner_label, voice.name)
        return base / "audio.wav", (base / "text.txt").read_text()

    def count_owned(self, key_id: int) -> int:
        with self.db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM voices WHERE owner_key_id = ?", (key_id,))
            return int(cur.fetchone()["n"])

    def register(self, caller: Caller, name: str, wav_bytes: bytes, ref_text: str) -> Voice:
        validate_name(name)
        if not caller.key.can_upload and not caller.is_admin:
            raise UploadNotAllowed("this key is not allowed to upload voices")

        existing = self._find(caller.id, name)
        if existing is None and not caller.is_admin:
            if self.count_owned(caller.id) >= caller.key.max_voices:
                raise VoiceLimitReached(
                    f"voice limit reached ({caller.key.max_voices}); delete one first",
                    extra={"limit": caller.key.max_voices},
                )

        target = self.dir_for(caller.label, name)
        target.mkdir(parents=True, exist_ok=True)
        (target / "audio.wav").write_bytes(wav_bytes)
        (target / "text.txt").write_text(ref_text)

        if existing is not None:
            return existing
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO voices (owner_key_id, name, visibility, created_at) "
                "VALUES (?, ?, 'private', ?)",
                (caller.id, name, now_iso()),
            )
        created = self._find(caller.id, name)
        assert created is not None
        return created

    def adopt(self, owner: Key, name: str) -> Voice:
        """Record a voice whose files are already on disk (migration path)."""
        existing = self._find(owner.id, name)
        if existing is not None:
            return existing
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO voices (owner_key_id, name, visibility, created_at) "
                "VALUES (?, ?, 'private', ?)",
                (owner.id, name, now_iso()),
            )
        adopted = self._find(owner.id, name)
        assert adopted is not None
        return adopted

    def _find(self, owner_id: int, name: str) -> Voice | None:
        with self.db.cursor() as cur:
            cur.execute(f"{_SELECT} WHERE v.owner_key_id = ? AND v.name = ?", (owner_id, name))
            row = cur.fetchone()
        return _row_to_voice(row) if row else None

    def all_voices(self) -> list[Voice]:
        with self.db.cursor() as cur:
            cur.execute(f"{_SELECT} ORDER BY k.label, v.name")
            return [_row_to_voice(r) for r in cur.fetchall()]

    def visible_to(self, caller: Caller) -> list[Voice]:
        if caller.is_admin:
            return self.all_voices()
        with self.db.cursor() as cur:
            cur.execute(
                f"""{_SELECT}
                    LEFT JOIN voice_grants g
                      ON g.voice_id = v.id AND g.grantee_key_id = ?
                    WHERE v.owner_key_id = ? OR v.visibility = 'public' OR g.voice_id IS NOT NULL
                    ORDER BY k.label, v.name""",
                (caller.id, caller.id),
            )
            return [_row_to_voice(r) for r in cur.fetchall()]

    def _can_see(self, caller: Caller, voice: Voice) -> bool:
        if caller.is_admin or voice.owner_id == caller.id or voice.visibility == "public":
            return True
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM voice_grants WHERE voice_id = ? AND grantee_key_id = ?",
                (voice.id, caller.id),
            )
            return cur.fetchone() is not None

    def resolve(self, caller: Caller, spec: str) -> Voice:
        if "/" in spec:
            owner_label, _, name = spec.partition("/")
            owner = self.keys.get_by_label(owner_label)
            voice = self._find(owner.id, name) if owner else None
            if voice is None or not self._can_see(caller, voice):
                raise VoiceNotFound(f"no voice {spec!r}")
            return voice

        own = self._find(caller.id, spec)
        if own is not None:
            return own

        candidates = [v for v in self.visible_to(caller) if v.name == spec]
        if not candidates:
            raise VoiceNotFound(f"no voice {spec!r}")
        if len(candidates) > 1:
            qualified = sorted(v.qualified for v in candidates)
            raise AmbiguousVoice(
                f"{spec!r} matches several voices; use a qualified name",
                extra={"candidates": qualified},
            )
        return candidates[0]

    def _require_owner(self, caller: Caller, spec: str) -> Voice:
        voice = self.resolve(caller, spec)
        if not caller.is_admin and voice.owner_id != caller.id:
            raise Forbidden(f"{voice.qualified} belongs to {voice.owner_label}")
        return voice

    def delete(self, caller: Caller, spec: str) -> Voice:
        voice = self._require_owner(caller, spec)
        with self.db.cursor() as cur:
            cur.execute("DELETE FROM voices WHERE id = ?", (voice.id,))
        shutil.rmtree(self.dir_for(voice.owner_label, voice.name), ignore_errors=True)
        return voice

    def set_visibility(self, caller: Caller, spec: str, visibility: str) -> Voice:
        if visibility not in VISIBILITIES:
            raise ValueError(f"visibility must be one of {sorted(VISIBILITIES)}")
        voice = self._require_owner(caller, spec)
        with self.db.cursor() as cur:
            cur.execute("UPDATE voices SET visibility = ? WHERE id = ?", (visibility, voice.id))
        refreshed = self._find(voice.owner_id, voice.name)
        assert refreshed is not None
        return refreshed

    def grant(self, caller: Caller, spec: str, grantee_label: str) -> None:
        voice = self._require_owner(caller, spec)
        grantee = self.keys.get_by_label(grantee_label)
        if grantee is None:
            raise VoiceNotFound(f"no key labeled {grantee_label!r}")
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO voice_grants (voice_id, grantee_key_id, granted_by, created_at) "
                "VALUES (?, ?, ?, ?)",
                (voice.id, grantee.id, caller.id, now_iso()),
            )

    def revoke_grant(self, caller: Caller, spec: str, grantee_label: str) -> None:
        voice = self._require_owner(caller, spec)
        grantee = self.keys.get_by_label(grantee_label)
        if grantee is None:
            raise VoiceNotFound(f"no key labeled {grantee_label!r}")
        with self.db.cursor() as cur:
            cur.execute(
                "DELETE FROM voice_grants WHERE voice_id = ? AND grantee_key_id = ?",
                (voice.id, grantee.id),
            )

    def grants_for(self, voice: Voice) -> list[str]:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT k.label FROM voice_grants g JOIN api_keys k ON k.id = g.grantee_key_id "
                "WHERE g.voice_id = ? ORDER BY k.label",
                (voice.id,),
            )
            return [r["label"] for r in cur.fetchall()]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest server/tests/test_voices.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add server/mimic_server/voices.py server/tests/test_voices.py
git commit -m "feat(server): add voice registry with ownership, visibility, and grants"
```

---

### Task 6: Usage recording and quota enforcement

**Files:**
- Create: `server/mimic_server/usage.py`
- Test: `server/tests/test_usage.py`

**Interfaces:**
- Consumes: `Database`, `Caller`, `QuotaExceeded`.
- Produces: `UsageTracker(db)` with `chars_today(key_id) -> int`, `check_quota(caller, chars) -> None`, `record(key_id, endpoint, chars, *, voice_id=None, audio_seconds=0.0, status=200) -> None`, `totals(key_id=None, since=None) -> list[dict]`, `events(key_id=None, since=None, limit=100) -> list[dict]`; `day_start_iso() -> str`, `next_day_iso() -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_usage.py
import pytest
from mimic_server.db import Database
from mimic_server.errors import QuotaExceeded
from mimic_server.identity import Caller, KeyStore
from mimic_server.usage import UsageTracker


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "mimic.db")
    db.migrate()
    keys = KeyStore(db)
    dave, _ = keys.create("dave", daily_char_quota=100)
    admin, _ = keys.create("root", role="admin", daily_char_quota=1)
    return UsageTracker(db), keys, Caller(dave), Caller(admin)


def test_chars_today_starts_at_zero(env):
    usage, _, dave, _ = env
    assert usage.chars_today(dave.id) == 0


def test_record_accumulates(env):
    usage, _, dave, _ = env
    usage.record(dave.id, "/tts", 30)
    usage.record(dave.id, "/tts", 12)
    assert usage.chars_today(dave.id) == 42


def test_check_quota_allows_up_to_the_limit(env):
    usage, _, dave, _ = env
    usage.record(dave.id, "/tts", 90)
    usage.check_quota(dave, 10)


def test_check_quota_raises_past_the_limit(env):
    usage, _, dave, _ = env
    usage.record(dave.id, "/tts", 95)
    with pytest.raises(QuotaExceeded) as exc:
        usage.check_quota(dave, 10)
    assert exc.value.extra["used"] == 95
    assert exc.value.extra["limit"] == 100
    assert "resets_at" in exc.value.extra


def test_admin_is_exempt(env):
    usage, _, _, admin = env
    usage.record(admin.id, "/tts", 5000)
    usage.check_quota(admin, 5000)


def test_zero_quota_means_unlimited(env):
    usage, keys, dave, _ = env
    keys.update("dave", daily_char_quota=0)
    refreshed = Caller(keys.get_by_label("dave"))
    usage.record(dave.id, "/tts", 10_000)
    usage.check_quota(refreshed, 10_000)


def test_totals_group_by_key(env):
    usage, _, dave, admin = env
    usage.record(dave.id, "/tts", 10, audio_seconds=1.5)
    usage.record(admin.id, "/tts", 5, audio_seconds=0.5)
    by_label = {row["label"]: row for row in usage.totals()}
    assert by_label["dave"]["chars"] == 10
    assert by_label["dave"]["requests"] == 1
    assert by_label["dave"]["audio_seconds"] == pytest.approx(1.5)


def test_events_filtered_by_key(env):
    usage, _, dave, admin = env
    usage.record(dave.id, "/tts", 10)
    usage.record(admin.id, "/clone/tts", 5)
    rows = usage.events(key_id=dave.id)
    assert len(rows) == 1
    assert rows[0]["endpoint"] == "/tts"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_usage.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Per-key usage recording and daily character quotas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mimic_server.errors import QuotaExceeded

if TYPE_CHECKING:
    from mimic_server.db import Database
    from mimic_server.identity import Caller


def day_start_iso() -> str:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def next_day_iso() -> str:
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight + timedelta(days=1)).isoformat()


class UsageTracker:
    def __init__(self, db: Database) -> None:
        self.db = db

    def chars_today(self, key_id: int) -> int:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(chars), 0) AS n FROM usage_events "
                "WHERE key_id = ? AND ts >= ?",
                (key_id, day_start_iso()),
            )
            return int(cur.fetchone()["n"])

    def check_quota(self, caller: Caller, chars: int) -> None:
        limit = caller.key.daily_char_quota
        if caller.is_admin or limit <= 0:
            return
        used = self.chars_today(caller.id)
        if used + chars > limit:
            raise QuotaExceeded(
                f"daily character quota exceeded ({used}/{limit})",
                extra={"used": used, "limit": limit, "resets_at": next_day_iso()},
            )

    def record(
        self,
        key_id: int,
        endpoint: str,
        chars: int,
        *,
        voice_id: int | None = None,
        audio_seconds: float = 0.0,
        status: int = 200,
    ) -> None:
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO usage_events (key_id, ts, endpoint, voice_id, chars, audio_seconds, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    key_id,
                    datetime.now(UTC).isoformat(),
                    endpoint,
                    voice_id,
                    chars,
                    audio_seconds,
                    status,
                ),
            )

    def totals(self, key_id: int | None = None, since: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if key_id is not None:
            clauses.append("u.key_id = ?")
            params.append(key_id)
        if since is not None:
            clauses.append("u.ts >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.cursor() as cur:
            cur.execute(
                f"""SELECT k.label AS label,
                           COUNT(*) AS requests,
                           COALESCE(SUM(u.chars), 0) AS chars,
                           COALESCE(SUM(u.audio_seconds), 0) AS audio_seconds
                    FROM usage_events u JOIN api_keys k ON k.id = u.key_id
                    {where}
                    GROUP BY k.label ORDER BY chars DESC""",  # noqa: S608
                params,
            )
            return [dict(r) for r in cur.fetchall()]

    def events(
        self, key_id: int | None = None, since: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if key_id is not None:
            clauses.append("u.key_id = ?")
            params.append(key_id)
        if since is not None:
            clauses.append("u.ts >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.cursor() as cur:
            cur.execute(
                f"""SELECT u.*, k.label AS label FROM usage_events u
                    JOIN api_keys k ON k.id = u.key_id
                    {where} ORDER BY u.ts DESC LIMIT ?""",  # noqa: S608
                [*params, limit],
            )
            return [dict(r) for r in cur.fetchall()]
```

The `# noqa: S608` markers are justified: only the WHERE *clause shape* is interpolated, assembled from a fixed set of literals; every value is a bound parameter.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest server/tests/test_usage.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add server/mimic_server/usage.py server/tests/test_usage.py
git commit -m "feat(server): add usage tracking and daily quota enforcement"
```

---

### Task 7: Bootstrap and migration from the flat reference dir

**Files:**
- Create: `server/mimic_server/bootstrap.py`
- Modify: `server/mimic_server/config.py`
- Test: `server/tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `Database`, `KeyStore`, `VoiceRegistry`, `Settings`.
- Produces: `bootstrap(settings) -> BootstrapResult` where `BootstrapResult` is a frozen dataclass of `db, keys, voices, root: Key`.
- New settings: `db_path: Path | None` (defaults to `$MIMIC_DATA_DIR/mimic.db`, else `mimic.db` beside the reference dir), `root_label: str = "root"`, `wyoming_key: str = ""`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_bootstrap.py
from mimic_server.bootstrap import bootstrap
from mimic_server.config import Settings


def _settings(tmp_path, **kw):
    return Settings(
        reference_dir=tmp_path / "reference",
        db_path=tmp_path / "mimic.db",
        **kw,
    )


def test_creates_root_key_from_env_token(tmp_path):
    result = bootstrap(_settings(tmp_path, api_token="s3cret"))
    assert result.root.label == "root"
    assert result.root.is_admin
    assert result.root.managed_by_env
    assert result.keys.authenticate("s3cret").id == result.root.id


def test_root_label_is_configurable(tmp_path):
    result = bootstrap(_settings(tmp_path, api_token="s3cret", root_label="jim"))
    assert result.root.label == "jim"


def test_dev_mode_still_gets_a_root_key(tmp_path):
    result = bootstrap(_settings(tmp_path))
    assert result.root.is_admin


def test_adopts_and_moves_legacy_flat_voices(tmp_path):
    reference = tmp_path / "reference"
    for name in ("jim", "piper"):
        legacy = reference / name
        legacy.mkdir(parents=True)
        (legacy / "audio.wav").write_bytes(b"RIFF" + name.encode())
        (legacy / "text.txt").write_text(f"hello from {name}")

    result = bootstrap(_settings(tmp_path, api_token="s3cret", root_label="jim"))

    assert sorted(v.qualified for v in result.voices.all_voices()) == ["jim/jim", "jim/piper"]
    moved = reference / "jim" / "piper" / "audio.wav"
    assert moved.read_bytes() == b"RIFFpiper"
    assert not (reference / "piper").exists()


def test_bootstrap_is_idempotent(tmp_path):
    reference = tmp_path / "reference"
    legacy = reference / "piper"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"RIFF")
    (legacy / "text.txt").write_text("t")

    settings = _settings(tmp_path, api_token="s3cret")
    bootstrap(settings)
    second = bootstrap(settings)

    assert [v.qualified for v in second.voices.all_voices()] == ["root/piper"]


def test_rotating_the_env_token_invalidates_the_old_one(tmp_path):
    bootstrap(_settings(tmp_path, api_token="old"))
    result = bootstrap(_settings(tmp_path, api_token="new"))
    assert result.keys.authenticate("old") is None
    assert result.keys.authenticate("new").is_admin
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_bootstrap.py -v`
Expected: FAIL — `ModuleNotFoundError`, and `Settings` has no `db_path`.

- [ ] **Step 3: Add the new settings**

In `server/mimic_server/config.py`, add a `_default_db_path()` factory mirroring `_default_reference_dir()` (`$MIMIC_DATA_DIR/mimic.db` when the env var is set, otherwise `mimic.db` in the cwd), and these fields to `Settings`:

```python
    db_path: Path = Field(default_factory=_default_db_path)
    root_label: str = "root"
    # Wyoming has no auth in-protocol, so it runs as a named key's identity.
    wyoming_key: str = ""
```

- [ ] **Step 4: Implement `bootstrap.py`**

The legacy move is done as copy-then-verify-then-remove rather than `shutil.move`, so a crash mid-migration leaves the original in place.

```python
"""One-time-per-boot setup: open the DB, seed the root key, adopt legacy voices."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mimic_server.db import Database
from mimic_server.identity import Key, KeyStore, generate_token
from mimic_server.voices import VALID_NAME, VoiceRegistry

if TYPE_CHECKING:
    from pathlib import Path

    from mimic_server.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapResult:
    db: Database
    keys: KeyStore
    voices: VoiceRegistry
    root: Key


def bootstrap(settings: Settings) -> BootstrapResult:
    db = Database(settings.db_path)
    db.migrate()
    keys = KeyStore(db)
    registry = VoiceRegistry(db, keys, settings.reference_dir)

    # In loopback dev mode there is no env token, but ownership still needs a
    # row to point at, so root gets an unguessable token nobody ever uses.
    root = keys.ensure_env_root(settings.api_token or generate_token(), settings.root_label)

    _adopt_legacy_voices(settings.reference_dir, root, registry)
    return BootstrapResult(db=db, keys=keys, voices=registry, root=root)


def _adopt_legacy_voices(reference_dir: Path, root: Key, registry: VoiceRegistry) -> None:
    """Move pre-multi-user `reference/<name>/` dirs under `reference/<root>/`."""
    if not reference_dir.is_dir():
        return
    for legacy in sorted(reference_dir.iterdir()):
        if legacy.name == root.label or not (legacy / "audio.wav").exists():
            continue
        if not VALID_NAME.match(legacy.name):
            logger.warning("skipping legacy voice dir with unusable name: %s", legacy.name)
            continue
        destination = registry.dir_for(root.label, legacy.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copytree(legacy, destination)
        if (destination / "audio.wav").exists():
            shutil.rmtree(legacy)
        registry.adopt(root, legacy.name)
        logger.info("adopted legacy voice %r as %s/%s", legacy.name, root.label, legacy.name)
```

Note the guard order: a directory already sitting under `reference/<root.label>/` is skipped, which is what makes re-running a no-op.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest server/tests/test_bootstrap.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
./lint.sh
git add server/mimic_server/bootstrap.py server/mimic_server/config.py server/tests/test_bootstrap.py
git commit -m "feat(server): bootstrap DB, root key, and legacy voice migration"
```

---

### Task 8: `current_caller` dependency and error handler

Replaces `require_token`. After this task the app authenticates against the DB, and `Services` carries the full object graph.

**Files:**
- Modify: `server/mimic_server/auth.py`
- Modify: `server/mimic_server/services.py`
- Modify: `server/mimic_server/app.py`
- Modify: `server/mimic_server/routes/*.py` (swap `dependencies=[svc.auth]` for a `caller` parameter)
- Delete: `server/tests/test_auth.py` (replaced)
- Test: `server/tests/test_caller.py`

**Interfaces:**
- Consumes: `KeyStore`, `Caller`, `Unauthorized`.
- Produces: `make_caller_dependency(settings, keys, root) -> Callable[..., Caller]`; `install_error_handler(app) -> None`; `Services` gains `db`, `keys`, `voices`, `usage`, `root`, and `caller` (a `Depends` marker for the dependency).

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_caller.py
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from mimic_server.auth import install_error_handler, make_caller_dependency
from mimic_server.bootstrap import bootstrap
from mimic_server.config import Settings
from mimic_server.errors import Forbidden
from mimic_server.identity import Caller


@pytest.fixture
def env(tmp_path):
    def build(**kw):
        settings = Settings(
            reference_dir=tmp_path / "reference", db_path=tmp_path / "mimic.db", **kw
        )
        result = bootstrap(settings)
        dependency = make_caller_dependency(settings, result.keys, result.root)
        app = FastAPI()
        install_error_handler(app)

        @app.get("/who")
        def who(caller: Annotated[Caller, Depends(dependency)]) -> dict[str, object]:
            return {"label": caller.label, "admin": caller.is_admin}

        @app.get("/boom")
        def boom() -> None:
            raise Forbidden("nope")

        return TestClient(app), result

    return build


def test_dev_mode_resolves_to_root(env):
    client, result = env()
    body = client.get("/who").json()
    assert body == {"label": result.root.label, "admin": True}


def test_missing_token_is_401_with_challenge(env):
    client, _ = env(api_token="s3cret")
    r = client.get("/who")
    assert r.status_code == 401
    assert "Bearer" in r.headers["WWW-Authenticate"]


def test_root_token_authenticates_as_admin(env):
    client, _ = env(api_token="s3cret")
    r = client.get("/who", headers={"Authorization": "Bearer s3cret"})
    assert r.json()["admin"] is True


def test_minted_key_authenticates_as_itself(env):
    client, result = env(api_token="s3cret")
    _, token = result.keys.create("dave")
    body = client.get("/who", headers={"Authorization": f"Bearer {token}"}).json()
    assert body == {"label": "dave", "admin": False}


def test_revoked_key_is_rejected(env):
    client, result = env(api_token="s3cret")
    _, token = result.keys.create("dave")
    result.keys.update("dave", enabled=False)
    assert client.get("/who", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_successful_request_records_last_used(env):
    client, result = env(api_token="s3cret")
    _, token = result.keys.create("dave")
    client.get("/who", headers={"Authorization": f"Bearer {token}"})
    assert result.keys.get_by_label("dave").last_used_at is not None


def test_domain_errors_map_to_status_and_payload(env):
    client, _ = env()
    r = client.get("/boom")
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "detail": "nope"}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_caller.py -v`
Expected: FAIL — `ImportError: cannot import name 'make_caller_dependency'`.

- [ ] **Step 3: Rewrite `auth.py`**

```python
"""Request authentication: resolve a bearer token to a Caller."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from mimic_server.errors import MimicError, Unauthorized
from mimic_server.identity import Caller

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

    from mimic_server.config import Settings
    from mimic_server.identity import Key, KeyStore

_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="mimic"'}


def make_caller_dependency(
    settings: Settings, keys: KeyStore, root: Key
) -> Callable[..., Caller]:
    """Build the dependency that turns a request into a Caller.

    With no MIMIC_API_TOKEN the server is loopback-only (enforced at startup),
    so every request resolves to root and the dev workflow is unchanged.
    """
    if not settings.auth_required:

        def _local_admin() -> Caller:
            return Caller(root)

        return _local_admin

    def _authenticate(authorization: str | None = Header(default=None)) -> Caller:
        if not authorization or not authorization.startswith("Bearer "):
            raise Unauthorized("missing bearer token")
        key = keys.authenticate(authorization.removeprefix("Bearer ").strip())
        if key is None:
            raise Unauthorized("invalid, revoked, or expired token")
        keys.touch(key.id)
        return Caller(key)

    return _authenticate


def require_admin(caller: Caller) -> Caller:
    from mimic_server.errors import Forbidden

    if not caller.is_admin:
        raise Forbidden("admin key required")
    return caller


def install_error_handler(app: FastAPI) -> None:
    @app.exception_handler(MimicError)
    async def _handle(_: Request, exc: MimicError) -> JSONResponse:
        headers = _CHALLENGE if exc.status == 401 else None
        return JSONResponse(status_code=exc.status, content=exc.payload(), headers=headers)
```

- [ ] **Step 4: Widen `Services` and rewire `build_app`**

```python
@dataclass
class Services:
    settings: Settings
    backend: TTSBackend
    db: Database
    keys: KeyStore
    voices: VoiceRegistry
    usage: UsageTracker
    root: Key
    caller: Any  # Depends(make_caller_dependency(...))
```

```python
def build_app(settings, backend_factory=None) -> FastAPI:
    _configure_environment(settings)
    backend = (backend_factory or make_backend)(settings)
    boot = bootstrap(settings)
    svc = Services(
        settings=settings,
        backend=backend,
        db=boot.db,
        keys=boot.keys,
        voices=boot.voices,
        usage=UsageTracker(boot.db),
        root=boot.root,
        caller=Depends(make_caller_dependency(settings, boot.keys, boot.root)),
    )
    app = FastAPI(title="mimic-tts API", lifespan=_make_lifespan(backend, settings))
    install_error_handler(app)
    for module in (system, tts, clones, openai):
        module.register(app, svc)
    _mount_web_ui(app)
    return app
```

- [ ] **Step 5: Convert every route to take a caller**

Mechanical change across `routes/`. Each protected handler drops `dependencies=[svc.auth]` and gains a parameter:

```python
    @app.post("/tts")
    async def tts(
        caller: Annotated[Caller, svc.caller],
        text: Annotated[str, Form()],
        ...
    ):
```

`/health` stays open. Handlers that don't use `caller` yet still declare it — Tasks 9-11 fill in the behavior, and declaring it now means the auth boundary is already correct.

- [ ] **Step 6: Update the existing app tests**

`server/tests/test_app.py` needs `db_path=tmp_path / "mimic.db"` added to its `_app` helper's `Settings(...)`. Delete `server/tests/test_auth.py`; `test_caller.py` supersedes it.

- [ ] **Step 7: Run the suite**

Run: `./lint.sh`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add -A server
git commit -m "feat(server): authenticate requests to a Caller backed by the key store"
```

---

### Task 9: Permission-aware clone routes

**Files:**
- Modify: `server/mimic_server/routes/clones.py`
- Test: `server/tests/test_routes_clones.py`

**Interfaces:**
- Consumes: `VoiceRegistry`, `Caller`, `UsageTracker`.
- Produces: `GET /clone/voices`, `POST /clone/register`, `DELETE /clone/voices/{spec:path}`, `PATCH /clone/voices/{spec:path}`, `POST /clone/voices/{spec:path}/grants`, `DELETE /clone/voices/{spec:path}/grants/{grantee}`.

Route paths use `{spec:path}` so qualified names like `dave/warm` bind as a single parameter.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_routes_clones.py
import io

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from mimic_server.app import build_app
from mimic_server.bootstrap import bootstrap
from mimic_server.config import Settings
from unittest.mock import MagicMock


def _wav() -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(12000, dtype=np.float32), 24000, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
def fake_backend():
    b = MagicMock()
    audio = np.zeros(1024, dtype=np.float32)
    b.builtin_voices.return_value = [{"name": "default", "language": "English"}]
    b.synth_builtin.return_value = (audio, 24000)
    b.synth_clone.return_value = (audio, 24000)
    b.synth_clone_oneshot.return_value = (audio, 24000)
    b.loaded_keys.return_value = []

    async def _no_lifecycle():
        return None

    b.run_lifecycle = _no_lifecycle
    return b


@pytest.fixture
def env(tmp_path, fake_backend):
    settings = Settings(
        reference_dir=tmp_path / "reference",
        db_path=tmp_path / "mimic.db",
        api_token="root-token",  # noqa: S106
    )
    app = build_app(settings, backend_factory=lambda _s: fake_backend)
    client = TestClient(app)
    keys = bootstrap(settings).keys
    _, dave = keys.create("dave")
    _, erin = keys.create("erin")
    return client, {"root": "root-token", "dave": dave, "erin": erin}, keys


def _auth(tokens, who):
    return {"Authorization": f"Bearer {tokens[who]}"}


def _register(client, tokens, who, name):
    return client.post(
        "/clone/register",
        headers=_auth(tokens, who),
        data={"name": name, "ref_text": "hello"},
        files={"ref_audio": ("a.wav", _wav(), "audio/wav")},
    )


def test_register_is_owned_by_the_caller(env):
    client, tokens, _ = env
    assert _register(client, tokens, "dave", "warm").status_code == 200
    body = client.get("/clone/voices", headers=_auth(tokens, "dave")).json()
    assert body["voices"] == ["dave/warm"]
    assert body["detail"][0] == {
        "name": "warm",
        "qualified": "dave/warm",
        "owner": "dave",
        "visibility": "private",
        "mine": True,
    }


def test_others_cannot_see_a_private_voice(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert client.get("/clone/voices", headers=_auth(tokens, "erin")).json()["voices"] == []


def test_admin_sees_every_voice(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert client.get("/clone/voices", headers=_auth(tokens, "root")).json()["voices"] == [
        "dave/warm"
    ]


def test_publish_then_everyone_sees_it(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.patch(
        "/clone/voices/warm", headers=_auth(tokens, "dave"), json={"visibility": "public"}
    )
    assert r.status_code == 200
    assert client.get("/clone/voices", headers=_auth(tokens, "erin")).json()["voices"] == [
        "dave/warm"
    ]


def test_grant_and_revoke(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert (
        client.post(
            "/clone/voices/warm/grants", headers=_auth(tokens, "dave"), json={"grantee": "erin"}
        ).status_code
        == 200
    )
    assert client.get("/clone/voices", headers=_auth(tokens, "erin")).json()["voices"] == [
        "dave/warm"
    ]
    assert (
        client.delete(
            "/clone/voices/warm/grants/erin", headers=_auth(tokens, "dave")
        ).status_code
        == 200
    )
    assert client.get("/clone/voices", headers=_auth(tokens, "erin")).json()["voices"] == []


def test_non_owner_grant_is_403(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    client.patch("/clone/voices/warm", headers=_auth(tokens, "dave"), json={"visibility": "public"})
    r = client.post(
        "/clone/voices/dave/warm/grants", headers=_auth(tokens, "erin"), json={"grantee": "erin"}
    )
    assert r.status_code == 403


def test_delete_someone_elses_private_voice_is_404(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert client.delete("/clone/voices/dave/warm", headers=_auth(tokens, "erin")).status_code == 404


def test_admin_can_delete_any_voice(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert client.delete("/clone/voices/dave/warm", headers=_auth(tokens, "root")).status_code == 200


def test_upload_blocked_when_can_upload_false(env):
    client, tokens, keys = env
    keys.update("dave", can_upload=False)
    r = _register(client, tokens, "dave", "warm")
    assert r.status_code == 403
    assert r.json()["error"] == "upload_not_allowed"


def test_max_voices_returns_409(env):
    client, tokens, keys = env
    keys.update("dave", max_voices=1)
    _register(client, tokens, "dave", "one")
    r = _register(client, tokens, "dave", "two")
    assert r.status_code == 409
    assert r.json()["error"] == "voice_limit_reached"


def test_no_endpoint_serves_reference_audio(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    for path in ("/clone/voices/dave/warm", "/clone/voices/dave/warm/audio.wav"):
        r = client.get(path, headers=_auth(tokens, "root"))
        assert r.status_code in (404, 405)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_routes_clones.py -v`
Expected: FAIL — the PATCH/grants routes don't exist and `/clone/voices` has no `detail`.

- [ ] **Step 3: Implement**

Delete `_resolve_clone`. The synthesis routes in this module move to Task 10; this step covers management only.

```python
class _VisibilityBody(BaseModel):
    visibility: str


class _GrantBody(BaseModel):
    grantee: str


def register(app: FastAPI, svc: Services) -> None:
    @app.get("/clone/voices")
    async def list_clone_voices(caller: Annotated[Caller, svc.caller]) -> dict[str, Any]:
        visible = svc.voices.visible_to(caller)
        return {
            "voices": [v.qualified for v in visible],
            "detail": [
                {
                    "name": v.name,
                    "qualified": v.qualified,
                    "owner": v.owner_label,
                    "visibility": v.visibility,
                    "mine": v.owner_id == caller.id,
                }
                for v in visible
            ],
        }

    @app.post("/clone/register")
    async def clone_register(
        caller: Annotated[Caller, svc.caller],
        ref_audio: Annotated[UploadFile, File()],
        ref_text: Annotated[str, Form()],
        name: Annotated[str, Form()] = "default",
    ) -> dict[str, str]:
        wav_bytes = transcode_to_wav(await ref_audio.read())
        voice = svc.voices.register(caller, name, wav_bytes, ref_text)
        svc.backend.drop_clone(voice.qualified) if hasattr(svc.backend, "drop_clone") else None
        return {"status": "ok", "name": voice.qualified}

    @app.delete("/clone/voices/{spec:path}")
    async def clone_delete(caller: Annotated[Caller, svc.caller], spec: str) -> dict[str, str]:
        voice = svc.voices.delete(caller, spec)
        with contextlib.suppress(AttributeError, KeyError):
            svc.backend.drop_clone(voice.qualified)
        return {"status": "ok", "name": voice.qualified}

    @app.patch("/clone/voices/{spec:path}")
    async def clone_set_visibility(
        caller: Annotated[Caller, svc.caller], spec: str, body: _VisibilityBody
    ) -> dict[str, str]:
        voice = svc.voices.set_visibility(caller, spec, body.visibility)
        return {"status": "ok", "name": voice.qualified, "visibility": voice.visibility}

    @app.post("/clone/voices/{spec:path}/grants")
    async def clone_grant(
        caller: Annotated[Caller, svc.caller], spec: str, body: _GrantBody
    ) -> dict[str, str]:
        svc.voices.grant(caller, spec, body.grantee)
        return {"status": "ok"}

    @app.delete("/clone/voices/{spec:path}/grants/{grantee}")
    async def clone_revoke(
        caller: Annotated[Caller, svc.caller], spec: str, grantee: str
    ) -> dict[str, str]:
        svc.voices.revoke_grant(caller, spec, grantee)
        return {"status": "ok"}
```

Two implementation notes:

- Route registration order matters. FastAPI matches in declaration order and `{spec:path}` is greedy, so the `/grants` routes must be declared **before** the bare `DELETE /clone/voices/{spec:path}`. Reorder accordingly; if `test_grant_and_revoke` fails with a 404 on the revoke, this is why.
- `ValueError` from `validate_name` and `set_visibility` should surface as 400, not 500. Add a second handler in `install_error_handler`:

```python
    @app.exception_handler(ValueError)
    async def _handle_value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "invalid_request", "detail": str(exc)})
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest server/tests/test_routes_clones.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add server/mimic_server/routes/clones.py server/mimic_server/auth.py server/tests/test_routes_clones.py
git commit -m "feat(server): permission-aware clone management routes"
```

---

### Task 10: Synthesis routes — resolution, quota, usage

**Files:**
- Modify: `server/mimic_server/routes/tts.py`
- Modify: `server/mimic_server/routes/clones.py` (`/clone/tts`, `/clone/oneshot`)
- Modify: `server/mimic_server/routes/openai.py`
- Create: `server/mimic_server/synth.py`
- Test: `server/tests/test_routes_synth.py`

**Interfaces:**
- Consumes: `VoiceRegistry`, `UsageTracker`, `Caller`, `Services`.
- Produces: `synth.synthesize(svc, caller, *, endpoint, text, voice_spec, language="English") -> tuple[np.ndarray, int]` — the single choke point where quota is checked, the voice is resolved (built-in or clone), synthesis runs, and usage is recorded.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_routes_synth.py
# Reuse the `env`, `fake_backend`, `_wav`, `_auth`, `_register` helpers from
# test_routes_clones.py by importing them:
from tests.test_routes_clones import _auth, _register, _wav, env, fake_backend  # noqa: F401


def test_builtin_tts_records_usage(env):
    client, tokens, _ = env
    assert client.post(
        "/tts", headers=_auth(tokens, "dave"), data={"text": "hello there"}
    ).status_code == 200
    r = client.get("/me", headers=_auth(tokens, "dave"))
    assert r.json()["usage_today"]["chars"] == len("hello there")


def test_clone_tts_with_own_voice(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.post("/clone/tts", headers=_auth(tokens, "dave"), data={"text": "hi", "name": "warm"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"


def test_clone_tts_with_someone_elses_private_voice_is_404(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.post(
        "/clone/tts", headers=_auth(tokens, "erin"), data={"text": "hi", "name": "dave/warm"}
    )
    assert r.status_code == 404


def test_clone_tts_works_after_a_grant(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    client.post("/clone/voices/warm/grants", headers=_auth(tokens, "dave"), json={"grantee": "erin"})
    r = client.post(
        "/clone/tts", headers=_auth(tokens, "erin"), data={"text": "hi", "name": "dave/warm"}
    )
    assert r.status_code == 200


def test_ambiguous_bare_name_is_409(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    _register(client, tokens, "erin", "warm")
    for who in ("dave", "erin"):
        client.patch(
            f"/clone/voices/{who}/warm", headers=_auth(tokens, "root"), json={"visibility": "public"}
        )
    r = client.post("/clone/tts", headers=_auth(tokens, "root"), data={"text": "hi", "name": "warm"})
    assert r.status_code == 409
    assert r.json()["candidates"] == ["dave/warm", "erin/warm"]


def test_quota_exceeded_is_429_and_blocks_synthesis(env):
    client, tokens, keys = env
    keys.update("dave", daily_char_quota=5)
    r = client.post("/tts", headers=_auth(tokens, "dave"), data={"text": "way too long"})
    assert r.status_code == 429
    body = r.json()
    assert body["error"] == "quota_exceeded"
    assert body["limit"] == 5
    assert "resets_at" in body


def test_admin_ignores_quota(env):
    client, tokens, keys = env
    keys.update("root", daily_char_quota=1)
    assert client.post(
        "/tts", headers=_auth(tokens, "root"), data={"text": "way too long"}
    ).status_code == 200


def test_openai_endpoint_honors_permissions(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    ok = client.post(
        "/v1/audio/speech",
        headers=_auth(tokens, "dave"),
        json={"input": "hi", "voice": "warm", "response_format": "wav"},
    )
    assert ok.status_code == 200
    denied = client.post(
        "/v1/audio/speech",
        headers=_auth(tokens, "erin"),
        json={"input": "hi", "voice": "dave/warm", "response_format": "wav"},
    )
    assert denied.status_code == 404


def test_oneshot_counts_against_quota(env):
    client, tokens, keys = env
    keys.update("dave", daily_char_quota=3)
    r = client.post(
        "/clone/oneshot",
        headers=_auth(tokens, "dave"),
        data={"text": "much longer than three", "ref_text": "hello"},
        files={"ref_audio": ("a.wav", _wav(), "audio/wav")},
    )
    assert r.status_code == 429
```

Add `server/tests/__init__.py` if the `from tests.test_routes_clones import ...` form doesn't resolve; alternatively move the shared fixtures into `server/tests/conftest.py` and drop the import. Prefer `conftest.py` — it is the cleaner of the two.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_routes_synth.py -v`
Expected: FAIL — no `/me`, no quota enforcement.

- [ ] **Step 3: Implement `synth.py`**

```python
"""The single path every synthesis request takes: quota, resolve, synth, record."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mimic_server.identity import Caller
    from mimic_server.services import Services


def synthesize(
    svc: Services,
    caller: Caller,
    *,
    endpoint: str,
    text: str,
    voice_spec: str,
    language: str = "English",
    instruct: str | None = None,
) -> tuple[Any, int]:
    svc.usage.check_quota(caller, len(text))

    builtin_names = {v["name"] for v in svc.backend.builtin_voices()}
    if voice_spec in builtin_names:
        voice_id = None
        samples, sample_rate = svc.backend.synth_builtin(
            text=text, speaker=voice_spec, language=language, instruct=instruct
        )
    else:
        voice = svc.voices.resolve(caller, voice_spec)
        voice_id = voice.id
        ref_path, ref_text = svc.voices.reference_paths(voice)
        samples, sample_rate = svc.backend.synth_clone(
            name=voice.qualified,
            text=text,
            ref_audio_path=ref_path,
            ref_text=ref_text,
            language=language,
        )

    svc.usage.record(
        caller.id,
        endpoint,
        len(text),
        voice_id=voice_id,
        audio_seconds=len(samples) / sample_rate if sample_rate else 0.0,
    )
    return samples, sample_rate
```

Then route every synthesis handler through it:

- `/tts` → `synthesize(..., endpoint="/tts", voice_spec=speaker, instruct=instruct or None)`
- `/clone/tts` → `synthesize(..., endpoint="/clone/tts", voice_spec=name)`
- `/v1/audio/speech` → `synthesize(..., endpoint="/v1/audio/speech", voice_spec=req.voice)`, then encode via `_OPENAI_FORMATS` as today
- `/clone/oneshot` → not a registered voice, so it calls `svc.usage.check_quota` / `svc.usage.record` directly around `backend.synth_clone_oneshot`

`_handle_openai_speech` loses its own built-in-vs-clone branch; that logic now lives in `synthesize`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest server/tests/test_routes_synth.py -v`
Expected: all pass except `test_builtin_tts_records_usage`, which needs `/me` from Task 11. Mark it `@pytest.mark.xfail(reason="/me lands in Task 11", strict=True)` and remove the marker in Task 11.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add server/mimic_server server/tests/test_routes_synth.py server/tests/conftest.py
git commit -m "feat(server): enforce voice permissions and quotas on synthesis"
```

---

### Task 11: `/me`, `/admin/*`, and the `/health` tightening

**Files:**
- Create: `server/mimic_server/routes/admin.py`
- Modify: `server/mimic_server/routes/system.py`
- Modify: `server/mimic_server/app.py` (register the admin module)
- Test: `server/tests/test_routes_admin.py`

**Interfaces:**
- Consumes: `KeyStore`, `VoiceRegistry`, `UsageTracker`, `require_admin`.
- Produces: `GET /me`; `POST|GET /admin/keys`; `PATCH|DELETE /admin/keys/{label}`; `GET /admin/usage`; `GET /admin/voices`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_routes_admin.py
from tests.conftest import _auth, _register  # provided by conftest.py from Task 10


def test_health_is_open_but_reveals_nothing(env):
    client, _, _ = env
    body = client.get("/health").json()
    assert set(body) == {"status", "backend", "stt_enabled"}


def test_me_reports_identity_and_quota(env):
    client, tokens, _ = env
    body = client.get("/me", headers=_auth(tokens, "dave")).json()
    assert body["label"] == "dave"
    assert body["role"] == "user"
    assert body["can_upload"] is True
    assert body["max_voices"] == 5
    assert body["daily_char_quota"] == 50000
    assert body["usage_today"] == {"requests": 0, "chars": 0, "audio_seconds": 0.0}


def test_non_admin_is_forbidden_from_admin_routes(env):
    client, tokens, _ = env
    for path in ("/admin/keys", "/admin/usage", "/admin/voices"):
        assert client.get(path, headers=_auth(tokens, "dave")).status_code == 403


def test_mint_returns_the_token_exactly_once(env):
    client, tokens, _ = env
    r = client.post("/admin/keys", headers=_auth(tokens, "root"), json={"label": "frank"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token.startswith("mk_")
    listing = client.get("/admin/keys", headers=_auth(tokens, "root")).json()["keys"]
    frank = next(k for k in listing if k["label"] == "frank")
    assert "token" not in frank
    assert frank["token_prefix"] == token[3:11]


def test_minted_key_works_immediately(env):
    client, tokens, _ = env
    token = client.post(
        "/admin/keys", headers=_auth(tokens, "root"), json={"label": "frank"}
    ).json()["token"]
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["label"] == "frank"


def test_duplicate_label_is_409(env):
    client, tokens, _ = env
    client.post("/admin/keys", headers=_auth(tokens, "root"), json={"label": "frank"})
    r = client.post("/admin/keys", headers=_auth(tokens, "root"), json={"label": "frank"})
    assert r.status_code == 409
    assert r.json()["error"] == "label_in_use"


def test_patch_adjusts_quotas(env):
    client, tokens, _ = env
    r = client.patch(
        "/admin/keys/dave", headers=_auth(tokens, "root"), json={"daily_char_quota": 10}
    )
    assert r.status_code == 200
    assert r.json()["daily_char_quota"] == 10


def test_revoke_disables_but_keeps_voices(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert client.delete("/admin/keys/dave", headers=_auth(tokens, "root")).status_code == 200
    assert client.get("/me", headers=_auth(tokens, "dave")).status_code == 401
    voices = client.get("/admin/voices", headers=_auth(tokens, "root")).json()["voices"]
    assert [v["qualified"] for v in voices] == ["dave/warm"]


def test_purge_removes_the_key_and_its_voices(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.delete("/admin/keys/dave?purge=true", headers=_auth(tokens, "root"))
    assert r.status_code == 200
    assert client.get("/admin/voices", headers=_auth(tokens, "root")).json()["voices"] == []


def test_root_key_cannot_be_revoked(env):
    client, tokens, _ = env
    r = client.delete("/admin/keys/root", headers=_auth(tokens, "root"))
    assert r.status_code == 403
    assert client.get("/me", headers=_auth(tokens, "root")).status_code == 200


def test_admin_voices_lists_owner_and_grants(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    client.post("/clone/voices/warm/grants", headers=_auth(tokens, "dave"), json={"grantee": "erin"})
    voices = client.get("/admin/voices", headers=_auth(tokens, "root")).json()["voices"]
    assert voices[0]["owner"] == "dave"
    assert voices[0]["grants"] == ["erin"]


def test_admin_usage_reports_per_key_totals(env):
    client, tokens, _ = env
    client.post("/tts", headers=_auth(tokens, "dave"), data={"text": "hello"})
    totals = client.get("/admin/usage", headers=_auth(tokens, "root")).json()["totals"]
    assert {"label": "dave", "requests": 1, "chars": 5, "audio_seconds": totals[0]["audio_seconds"]} in totals
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_routes_admin.py -v`
Expected: FAIL — routes missing.

- [ ] **Step 3: Tighten `/health` and add `/me`**

In `routes/system.py`:

```python
    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Deliberately anonymous and deliberately uninformative — it is the one
        unauthenticated endpoint, so it must not enumerate voices or models."""
        return {
            "status": "ok",
            "backend": svc.settings.backend,
            "stt_enabled": bool(svc.settings.stt_uri),
        }

    @app.get("/me")
    async def me(caller: Annotated[Caller, svc.caller]) -> dict[str, Any]:
        totals = svc.usage.totals(key_id=caller.id, since=day_start_iso())
        today = totals[0] if totals else {"requests": 0, "chars": 0, "audio_seconds": 0.0}
        return {
            "label": caller.label,
            "role": caller.key.role,
            "can_upload": caller.key.can_upload,
            "max_voices": caller.key.max_voices,
            "voices_used": svc.voices.count_owned(caller.id),
            "daily_char_quota": caller.key.daily_char_quota,
            "usage_today": {
                "requests": today["requests"],
                "chars": today["chars"],
                "audio_seconds": today["audio_seconds"],
            },
            "models_loaded": svc.backend.loaded_keys() if caller.is_admin else None,
        }
```

- [ ] **Step 4: Implement `routes/admin.py`**

Every handler takes `caller: Annotated[Caller, svc.caller]` and calls `require_admin(caller)` as its first statement.

```python
class _MintBody(BaseModel):
    label: str
    role: str = "user"
    can_upload: bool = True
    max_voices: int = DEFAULT_MAX_VOICES
    daily_char_quota: int = DEFAULT_DAILY_CHAR_QUOTA
    expires_at: str | None = None
    notes: str = ""


class _PatchBody(BaseModel):
    enabled: bool | None = None
    can_upload: bool | None = None
    max_voices: int | None = None
    daily_char_quota: int | None = None
    expires_at: str | None = None
    role: str | None = None
    notes: str | None = None


def _key_json(key: Key, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "label": key.label,
        "token_prefix": key.token_prefix,
        "role": key.role,
        "enabled": key.enabled,
        "created_at": key.created_at,
        "last_used_at": key.last_used_at,
        "expires_at": key.expires_at,
        "can_upload": key.can_upload,
        "max_voices": key.max_voices,
        "daily_char_quota": key.daily_char_quota,
        "managed_by_env": key.managed_by_env,
        "notes": key.notes,
        "usage": usage or {"requests": 0, "chars": 0, "audio_seconds": 0.0},
    }
```

`POST /admin/keys` returns `{**_key_json(key), "token": plaintext}` — the only response in the system that ever contains a token.

`DELETE /admin/keys/{label}` takes `purge: bool = False`. It raises `Forbidden("the root key is managed by MIMIC_API_TOKEN and cannot be revoked")` when `key.managed_by_env`. Without `purge` it calls `keys.update(label, enabled=False)`. With `purge` it removes each of the key's voice directories from disk and then `keys.delete(label)` — the `ON DELETE CASCADE` on `voices` and `voice_grants` clears the rows.

`GET /admin/usage` accepts `key` (label) and `since` (ISO string) query params and returns `{"totals": [...], "events": [...]}`, with `events` capped by a `limit: int = 100` param.

`GET /admin/voices` returns `{"voices": [{"qualified", "name", "owner", "visibility", "created_at", "grants"}]}` built from `svc.voices.all_voices()` plus `grants_for`.

Register the module in `build_app`'s tuple: `for module in (system, tts, clones, openai, admin):`.

- [ ] **Step 5: Remove the xfail from Task 10**

Delete the `@pytest.mark.xfail` marker on `test_builtin_tts_records_usage`; `/me` now exists.

- [ ] **Step 6: Run the suite**

Run: `./lint.sh`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add -A server
git commit -m "feat(server): add /me and admin key, usage, and voice endpoints"
```

---

### Task 12: Wyoming runs as a configured identity

**Files:**
- Modify: `server/mimic_server/wyoming_server.py`
- Modify: `server/mimic_server/app.py` (`_make_lifespan` passes `svc`)
- Test: `server/tests/test_wyoming.py` (extend)

**Interfaces:**
- Consumes: `Services`, `KeyStore`, `Caller`, `synth.synthesize`.
- Produces: `run_wyoming_server(svc: Services) -> None` (signature change from `(backend, settings)`); `resolve_wyoming_caller(svc) -> Caller`.

- [ ] **Step 1: Write the failing tests**

```python
def test_wyoming_caller_defaults_to_root(tmp_path, fake_backend):
    svc = _services(tmp_path, fake_backend, api_token="s3cret")
    assert resolve_wyoming_caller(svc).label == svc.root.label


def test_wyoming_caller_uses_the_configured_label(tmp_path, fake_backend):
    svc = _services(tmp_path, fake_backend, api_token="s3cret", wyoming_key="ha")
    svc.keys.create("ha")
    assert resolve_wyoming_caller(svc).label == "ha"


def test_unknown_wyoming_label_falls_back_to_root_with_a_warning(tmp_path, fake_backend, caplog):
    svc = _services(tmp_path, fake_backend, api_token="s3cret", wyoming_key="ghost")
    with caplog.at_level("WARNING"):
        caller = resolve_wyoming_caller(svc)
    assert caller.label == svc.root.label
    assert "ghost" in caplog.text


def test_wyoming_synthesis_is_attributed_to_its_key(tmp_path, fake_backend):
    svc = _services(tmp_path, fake_backend, api_token="s3cret", wyoming_key="ha")
    svc.keys.create("ha")
    caller = resolve_wyoming_caller(svc)
    synthesize(svc, caller, endpoint="wyoming", text="hello", voice_spec="default")
    assert svc.usage.chars_today(caller.id) == 5
```

Write `_services(tmp_path, fake_backend, **kw)` as a small helper that builds `Settings`, runs `bootstrap`, and assembles a `Services` — the same assembly `build_app` performs. Put it in `conftest.py` and have `build_app` call it too, so the two can't drift.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_wyoming.py -v`
Expected: FAIL — `resolve_wyoming_caller` doesn't exist.

- [ ] **Step 3: Implement**

```python
def resolve_wyoming_caller(svc: Services) -> Caller:
    """Wyoming's protocol carries no credentials, so the server runs as a
    named key. Port 10200 stays LAN-only regardless; this bounds the blast
    radius, it does not replace the network boundary."""
    label = svc.settings.wyoming_key
    if label:
        key = svc.keys.get_by_label(label)
        if key is not None:
            return Caller(key)
        logger.warning(
            "MIMIC_WYOMING_KEY=%r does not match any key; falling back to root", label
        )
    return Caller(svc.root)
```

Change `run_wyoming_server(backend, settings)` to `run_wyoming_server(svc)`, resolve the caller once at startup, and replace its direct `backend.synth_*` calls with `synthesize(svc, caller, endpoint="wyoming", text=..., voice_spec=...)`. Update `_make_lifespan` to close over `svc` and call `run_wyoming_server(svc)`.

Wyoming voice listing should report `svc.voices.visible_to(caller)` qualified names plus built-ins, so HA's voice picker reflects the configured identity's permissions.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest server/tests/test_wyoming.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add server/mimic_server server/tests/test_wyoming.py
git commit -m "feat(server): run the Wyoming server as a configured key identity"
```

---

### Task 13: Authorization matrix, live end-to-end check, and docs

**Files:**
- Create: `server/tests/test_authorization_matrix.py`
- Create: `scripts/e2e_multi_user.sh`
- Modify: `docs/server.md`
- Modify: `README.md`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: everything above.
- Produces: no new code interfaces.

- [ ] **Step 1: Write the authorization matrix test**

One parametrized test covering every endpoint against anonymous, user, other-user, and admin callers. This is the regression net for the whole feature.

```python
# server/tests/test_authorization_matrix.py
import pytest

ANONYMOUS, OWNER, OTHER, ADMIN = "anonymous", "dave", "erin", "root"

# (method, path, body-kind, {actor: expected_status})
CASES = [
    ("GET", "/health", None, {ANONYMOUS: 200, OWNER: 200, OTHER: 200, ADMIN: 200}),
    ("GET", "/me", None, {ANONYMOUS: 401, OWNER: 200, OTHER: 200, ADMIN: 200}),
    ("GET", "/voices", None, {ANONYMOUS: 401, OWNER: 200, OTHER: 200, ADMIN: 200}),
    ("GET", "/clone/voices", None, {ANONYMOUS: 401, OWNER: 200, OTHER: 200, ADMIN: 200}),
    ("GET", "/admin/keys", None, {ANONYMOUS: 401, OWNER: 403, OTHER: 403, ADMIN: 200}),
    ("GET", "/admin/usage", None, {ANONYMOUS: 401, OWNER: 403, OTHER: 403, ADMIN: 200}),
    ("GET", "/admin/voices", None, {ANONYMOUS: 401, OWNER: 403, OTHER: 403, ADMIN: 200}),
    ("POST", "/admin/keys", "mint", {ANONYMOUS: 401, OWNER: 403, OTHER: 403, ADMIN: 200}),
    # dave owns a private voice "warm"; erin must not reach it by any route
    ("POST", "/clone/tts", "synth_warm", {ANONYMOUS: 401, OWNER: 200, OTHER: 404, ADMIN: 200}),
    ("PATCH", "/clone/voices/dave/warm", "publish", {ANONYMOUS: 401, OWNER: 200, OTHER: 404, ADMIN: 200}),
    ("DELETE", "/clone/voices/dave/warm", None, {ANONYMOUS: 401, OWNER: 200, OTHER: 404, ADMIN: 200}),
]


@pytest.mark.parametrize(("method", "path", "body_kind", "expected"), CASES)
@pytest.mark.parametrize("actor", [ANONYMOUS, OWNER, OTHER, ADMIN])
def test_authorization_matrix(matrix_env, method, path, body_kind, expected, actor):
    """Each case runs against a freshly seeded server so destructive verbs
    (DELETE, PATCH) can't leak state into the next actor's expectation."""
    client, tokens = matrix_env()
    headers = {} if actor == ANONYMOUS else {"Authorization": f"Bearer {tokens[actor]}"}
    kwargs = _body_for(body_kind, actor)
    response = client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == expected[actor], response.text
```

`matrix_env` is a factory fixture in `conftest.py` that builds a fresh app per call and pre-registers `dave/warm` as private. `_body_for` returns the right `data=`/`json=` kwargs per body kind:

```python
def _body_for(kind, actor):
    if kind == "mint":
        return {"json": {"label": f"minted-by-{actor}"}}
    if kind == "synth_warm":
        return {"data": {"text": "hi", "name": "dave/warm"}}
    if kind == "publish":
        return {"json": {"visibility": "public"}}
    return {}
```

Add one standalone test alongside it:

```python
def test_reference_audio_is_never_downloadable(matrix_env):
    client, tokens = matrix_env()
    admin = {"Authorization": f"Bearer {tokens['root']}"}
    probes = [
        "/clone/voices/dave/warm/audio.wav",
        "/clone/voices/dave/warm/text.txt",
        "/reference/dave/warm/audio.wav",
    ]
    for path in probes:
        r = client.get(path, headers=admin)
        assert r.status_code in (404, 405), path
        assert b"RIFF" not in r.content
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/pytest server/tests/test_authorization_matrix.py -v`
Expected: all pass. Any failure here is a real authorization bug — fix the module, never the expectation.

- [ ] **Step 3: Write the live end-to-end script**

Per `CLAUDE.md`, the bug-level confidence comes from hitting a real server the way a real user does, not from `TestClient`. `scripts/e2e_multi_user.sh` takes `$MIMIC_URL` and `$MIMIC_ADMIN_TOKEN` and walks the actual story with `curl`, asserting status codes with `set -e` and a `expect_status` helper:

1. `POST /admin/keys` label `e2e-friend` → capture the token.
2. As the friend: `GET /me` → 200, role `user`.
3. As the friend: `POST /clone/register` a short WAV → 200.
4. As the friend: `POST /clone/tts` with their own voice → 200, response is a WAV.
5. As the friend: `GET /clone/voices` → does not contain any admin-owned voice.
6. As the friend: `POST /clone/tts` naming an admin-owned private voice → **404**.
7. As admin: `POST /clone/voices/<admin>/<voice>/grants` granting `e2e-friend`.
8. As the friend: same call as step 6 → now **200**.
9. As admin: `DELETE /admin/keys/e2e-friend`.
10. As the friend: `GET /me` → **401**.
11. Cleanup: `DELETE /admin/keys/e2e-friend?purge=true`.

Steps 6 and 8 are the whole feature. If they don't behave, nothing else matters.

- [ ] **Step 4: Update the docs**

- `docs/server.md` — a "Multi-user access" section: the key lifecycle, the visibility/grant model, the resolution rules for bare vs qualified names, the new env vars (`MIMIC_ROOT_LABEL`, `MIMIC_WYOMING_KEY`, `MIMIC_DB_PATH`), and an explicit statement that reference audio is never downloadable.
- `docs/server.md` — an "Upgrading from single-token" note: the DB is created automatically on first boot, existing voices are adopted by the root key and moved under `reference/<root-label>/`, and `MIMIC_API_TOKEN` keeps working as the admin key.
- `README.md` — add multi-user key management to the bullet list at the top.
- `docker-compose.yml` — comment the new env vars.

- [ ] **Step 5: Full verification**

Run: `./lint.sh`
Expected: all green.

Then a real boot against a scratch data dir, confirming migration on a copy of a realistic tree:

```bash
rm -rf /tmp/mimic-e2e && mkdir -p /tmp/mimic-e2e/reference/legacyvoice
cp <a real wav> /tmp/mimic-e2e/reference/legacyvoice/audio.wav
echo "hello" > /tmp/mimic-e2e/reference/legacyvoice/text.txt
MIMIC_DATA_DIR=/tmp/mimic-e2e MIMIC_API_TOKEN=dev-admin MIMIC_BACKEND=<available backend> \
  .venv/bin/python -m mimic_server
```

Confirm the log reports the adopted voice, `reference/root/legacyvoice/audio.wav` exists, and `curl -s localhost:8000/health` returns exactly three fields.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "test(server): authorization matrix and multi-user e2e script; docs"
```

---

## Self-review notes

Spec coverage check, section by section:

- Storage / schema → Task 2. Token format, root key, dev mode → Tasks 4, 7, 8.
- Naming, on-disk layout, access rules, "reference audio never served" → Tasks 5, 9, 13.
- Quotas and defaults → Tasks 4 (defaults), 6 (enforcement), 10 (wiring).
- API surface: `/me` and `/admin/*` → Task 11; clone management → Task 9; synthesis → Task 10; `/health` tightening and the backward-compatible `/clone/voices` shape → Tasks 11 and 9.
- Wyoming → Task 12. Code structure → Tasks 1 and 8. Migration → Task 7. Error handling table → Tasks 3, 8, 9. Testing → every task, consolidated in 13.

The client CLI is deliberately absent — it is Plan 6.

**Known rough edge, decide during Task 5:** the last parametrized case in `test_invalid_names_rejected` splits its expectation on whether the name contains `/`. If that reads as fragile while implementing, make `register` reject any `/` in the name with `ValueError` and simplify the test to expect `ValueError` throughout.
