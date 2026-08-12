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

TOKEN_PREFIX = "mk_"  # noqa: S105 -- a public format tag, not a secret
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
