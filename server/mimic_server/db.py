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
