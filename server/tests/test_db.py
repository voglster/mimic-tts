from __future__ import annotations

from mimic_server.db import Database


def test_migrate_creates_tables(tmp_path):
    db = Database(tmp_path / "mimic.db")
    db.migrate()
    with db.cursor() as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row["name"] for row in cur.fetchall()}
    assert {"api_keys", "voices", "voice_grants", "usage_events", "schema_version"} <= tables


def test_migrate_is_idempotent(tmp_path):
    from mimic_server.db import MIGRATIONS

    db = Database(tmp_path / "mimic.db")
    db.migrate()
    db.migrate()
    with db.cursor() as cur:
        cur.execute("SELECT version FROM schema_version")
        assert cur.fetchone()["version"] == len(MIGRATIONS)


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
        cur.execute(
            "INSERT INTO voices (owner_key_id, name, visibility, created_at) "
            "VALUES (999, 'x', 'private', 'now')"
        )
