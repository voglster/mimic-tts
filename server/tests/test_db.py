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


def test_failed_migration_leaves_no_trace(tmp_path, monkeypatch):
    import sqlite3

    import mimic_server.db as db_module
    import pytest

    broken_migration = [
        ["CREATE TABLE probe (id INTEGER PRIMARY KEY)", "NOT VALID SQL"],
    ]
    monkeypatch.setattr(db_module, "MIGRATIONS", broken_migration)

    db = db_module.Database(tmp_path / "mimic.db")
    with pytest.raises(sqlite3.OperationalError):
        db.migrate()

    with db.cursor() as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = 'probe'")
        assert cur.fetchone() is None
        cur.execute("SELECT COUNT(*) AS n FROM schema_version")
        assert cur.fetchone()["n"] == 0


def test_migrate_resumes_from_partial_list(tmp_path, monkeypatch):
    import mimic_server.db as db_module

    path = tmp_path / "mimic.db"
    first_migration = [["CREATE TABLE first (id INTEGER PRIMARY KEY)"]]
    monkeypatch.setattr(db_module, "MIGRATIONS", first_migration)

    db = db_module.Database(path)
    db.migrate()
    with db.cursor() as cur:
        cur.execute("SELECT MAX(version) AS version FROM schema_version")
        assert cur.fetchone()["version"] == 1

    second_migration = [*first_migration, ["CREATE TABLE second (id INTEGER PRIMARY KEY)"]]
    monkeypatch.setattr(db_module, "MIGRATIONS", second_migration)

    db.migrate()
    with db.cursor() as cur:
        cur.execute("SELECT MAX(version) AS version FROM schema_version")
        assert cur.fetchone()["version"] == 2
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row["name"] for row in cur.fetchall()}
    assert {"first", "second"} <= tables
