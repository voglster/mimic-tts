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
