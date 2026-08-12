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
                "INSERT INTO usage_events "
                "(key_id, ts, endpoint, voice_id, chars, audio_seconds, status) "
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
        clauses: list[str] = []
        params: list[Any] = []
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
        clauses: list[str] = []
        params: list[Any] = []
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
