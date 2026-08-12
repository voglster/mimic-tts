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
        # Re-fetch rather than trust caller.key: the Caller passed in may be a
        # snapshot taken before an admin changed can_upload/max_voices on this key.
        key = self.keys.get_by_id(caller.id)
        assert key is not None
        if not key.can_upload and not key.is_admin:
            raise UploadNotAllowed("this key is not allowed to upload voices")

        existing = self._find(caller.id, name)
        if existing is None and not key.is_admin and self.count_owned(caller.id) >= key.max_voices:
            raise VoiceLimitReached(
                f"voice limit reached ({key.max_voices}); delete one first",
                extra={"limit": key.max_voices},
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
