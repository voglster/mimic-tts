from unittest.mock import MagicMock

import pytest
import typer
from mimic.admin_cli import _parse_expiry
from mimic.cli import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMIC_SERVER_URL", raising=False)
    monkeypatch.delenv("MIMIC_API_TOKEN", raising=False)
    monkeypatch.setenv("MIMIC_CONFIG_DIR", str(tmp_path))


def _stub_client(
    monkeypatch,
    *,
    create_key=None,
    list_keys=None,
    revoke_key=None,
    admin_usage=None,
    admin_voices=None,
):
    """Patch mimic.admin_cli.Client with a fake that records mutating calls."""
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)

    calls: list[tuple] = []
    fake.calls = calls

    if create_key is not None:

        def _create_key(label, **fields):
            calls.append(("create_key", label, fields))
            return create_key

        fake.create_key.side_effect = _create_key
    if list_keys is not None:
        fake.list_keys.return_value = list_keys
    if revoke_key is not None:

        def _revoke_key(label, *, purge=False):
            calls.append(("revoke_key", label, purge))
            return revoke_key

        fake.revoke_key.side_effect = _revoke_key
    if admin_usage is not None:
        fake.admin_usage.return_value = admin_usage
    if admin_voices is not None:
        fake.admin_voices.return_value = admin_voices

    monkeypatch.setattr("mimic.cli.Client", lambda **_kwargs: fake)
    return fake


def test_key_create_prints_the_token_with_a_warning(monkeypatch):
    stub = _stub_client(monkeypatch, create_key={"label": "dave", "token": "mk_secret123"})
    result = runner.invoke(app, ["admin", "key", "create", "dave"])
    assert result.exit_code == 0
    assert "mk_secret123" in result.stdout
    assert "shown once" in result.stdout.lower()
    assert stub.calls == [("create_key", "dave", {})]


def test_key_create_passes_only_the_options_given(monkeypatch):
    stub = _stub_client(monkeypatch, create_key={"label": "dave", "token": "mk_x"})
    runner.invoke(app, ["admin", "key", "create", "dave", "--quota", "100", "--no-upload"])
    assert stub.calls == [("create_key", "dave", {"daily_char_quota": 100, "can_upload": False})]


def test_key_create_admin_role(monkeypatch):
    stub = _stub_client(monkeypatch, create_key={"label": "co", "token": "mk_x"})
    runner.invoke(app, ["admin", "key", "create", "co", "--admin"])
    assert stub.calls == [("create_key", "co", {"role": "admin"})]


def test_key_create_help_states_max_voices_zero_blocks_uploads():
    """`--max-voices 0` blocks all uploads, unlike `--quota 0` which is unlimited.

    The server has no `max_voices <= 0 means unlimited` short-circuit — only
    `daily_char_quota` does — so the help text must not claim otherwise.
    """
    result = runner.invoke(app, ["admin", "key", "create", "--help"])
    assert result.exit_code == 0
    flattened = " ".join(result.stdout.replace("│", " ").split())
    assert "0 = no uploads allowed" in flattened
    max_voices_help = flattened.split("--max-voices")[1].split("--no-upload")[0]
    assert "unlimited" not in max_voices_help


def test_keys_lists_a_table(monkeypatch):
    _stub_client(
        monkeypatch,
        list_keys=[
            {
                "label": "root",
                "token_prefix": "abcd1234",
                "role": "admin",
                "enabled": True,
                "last_used_at": "2026-08-11T10:00:00+00:00",
                "daily_char_quota": 0,
                "usage": {"requests": 4, "chars": 900, "audio_seconds": 30.0},
            },
            {
                "label": "dave",
                "token_prefix": "efgh5678",
                "role": "user",
                "enabled": False,
                "last_used_at": None,
                "daily_char_quota": 50000,
                "usage": {"requests": 0, "chars": 0, "audio_seconds": 0.0},
            },
        ],
    )
    out = runner.invoke(app, ["admin", "keys"]).stdout
    assert "root" in out
    assert "dave" in out
    assert "revoked" in out
    assert "mk_efgh5678" in out or "efgh5678" in out


def test_key_revoke_defaults_to_soft(monkeypatch):
    stub = _stub_client(monkeypatch, revoke_key={"status": "ok"})
    assert runner.invoke(app, ["admin", "key", "revoke", "dave"]).exit_code == 0
    assert stub.calls == [("revoke_key", "dave", False)]


def test_key_revoke_purge_requires_confirmation(monkeypatch):
    stub = _stub_client(monkeypatch, revoke_key={"status": "ok"})
    declined = runner.invoke(app, ["admin", "key", "revoke", "dave", "--purge"], input="n\n")
    assert stub.calls == []
    assert declined.exit_code != 0 or "aborted" in declined.stdout.lower()

    accepted = runner.invoke(app, ["admin", "key", "revoke", "dave", "--purge"], input="y\n")
    assert accepted.exit_code == 0
    assert stub.calls == [("revoke_key", "dave", True)]


def test_usage_prints_totals(monkeypatch):
    _stub_client(
        monkeypatch,
        admin_usage={
            "totals": [{"label": "dave", "requests": 3, "chars": 1200, "audio_seconds": 40.0}],
            "events": [],
        },
    )
    out = runner.invoke(app, ["admin", "usage"]).stdout
    assert "dave" in out
    assert "1,200" in out


def test_voices_shows_owner_visibility_and_grants(monkeypatch):
    _stub_client(
        monkeypatch,
        admin_voices=[
            {
                "qualified": "jim/piper",
                "owner": "jim",
                "visibility": "private",
                "created_at": "2026-08-01T00:00:00+00:00",
                "grants": ["dave", "erin"],
            },
        ],
    )
    out = runner.invoke(app, ["admin", "voices"]).stdout
    assert "jim/piper" in out
    assert "dave, erin" in out


def test_parse_expiry_accepts_a_date():
    assert _parse_expiry("2027-01-01").startswith("2027-01-01T00:00:00")


def test_parse_expiry_accepts_a_duration():
    result = _parse_expiry("90d")
    assert result > _parse_expiry("1d")


def test_parse_expiry_rejects_garbage():
    with pytest.raises(typer.BadParameter):
        _parse_expiry("soonish")
