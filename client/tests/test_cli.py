from unittest.mock import MagicMock, patch

import pytest
from mimic.cli import app
from mimic.errors import (
    MimicForbiddenError,
    MimicNotFoundError,
    MimicQuotaError,
    MimicValidationError,
)
from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMIC_SERVER_URL", raising=False)
    monkeypatch.delenv("MIMIC_API_TOKEN", raising=False)
    monkeypatch.setenv("MIMIC_CONFIG_DIR", str(tmp_path))


def _stub_client(
    monkeypatch,
    *,
    whoami=None,
    clone_detail=None,
    clones=None,
    say_raises=None,
):
    """Patch mimic.cli.Client with a fake that records mutating calls in `.calls`."""
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)

    calls: list[tuple] = []
    fake.calls = calls

    if whoami is not None:
        fake.whoami.return_value = whoami
    if clone_detail is not None:
        fake.list_clone_detail.return_value = clone_detail
    if clones is not None:
        fake.list_clones.return_value = clones
    if say_raises is not None:
        fake.tts.side_effect = say_raises
        fake.clone_tts.side_effect = say_raises

    def _recorder(name):
        def _f(*args):
            calls.append((name, *args))
            return {}

        return _f

    fake.grant_voice.side_effect = _recorder("grant_voice")
    fake.set_visibility.side_effect = _recorder("set_visibility")
    fake.revoke_voice_grant.side_effect = _recorder("revoke_voice_grant")

    monkeypatch.setattr("mimic.cli.Client", lambda **_kwargs: fake)
    return fake


def test_voices_lists_built_in(runner):
    fake = MagicMock()
    fake.list_voices.return_value = [
        {"name": "Ryan", "language": "English"},
        {"name": "Aiden", "language": "English"},
    ]
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["voices"])
    assert r.exit_code == 0
    assert "Ryan" in r.stdout
    assert "Aiden" in r.stdout


def test_clones_lists_registered(runner):
    fake = MagicMock()
    fake.list_clones.return_value = ["alice", "bob"]
    fake.list_clone_detail.return_value = []
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["clones"])
    assert r.exit_code == 0
    assert "alice" in r.stdout
    assert "bob" in r.stdout


def test_health(runner):
    fake = MagicMock()
    fake.health.return_value = {"status": "ok", "models_loaded": ["clone"], "registered_voices": []}
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["health"])
    assert r.exit_code == 0
    assert "ok" in r.stdout


def test_say_with_out_writes_file(runner, tmp_path):
    """`mimic say <text> --out FILE` writes the wav and does NOT play."""
    fake = MagicMock()
    fake.tts.return_value = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 100
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)
    out = tmp_path / "out.wav"
    with (
        patch("mimic.cli.Client", return_value=fake),
        patch("mimic.cli._play_wav_bytes") as play_fn,
    ):
        r = runner.invoke(app, ["say", "hello", "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    assert out.exists()
    fake.tts.assert_called_once()
    play_fn.assert_not_called()


def test_say_without_out_plays_audio(runner):
    """`mimic say <text>` (no --out) plays the wav rather than writing a file."""
    fake = MagicMock()
    wav_bytes = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 100
    fake.tts.return_value = wav_bytes
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)
    with (
        patch("mimic.cli.Client", return_value=fake),
        patch("mimic.cli._play_wav_bytes") as play_fn,
    ):
        r = runner.invoke(app, ["say", "hello"])
    assert r.exit_code == 0, r.stdout
    play_fn.assert_called_once_with(wav_bytes)


def test_say_default_voice_from_config(runner, tmp_path):
    fake = MagicMock()
    fake.tts.return_value = b"RIFF" + b"\x00" * 100
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)
    out = tmp_path / "out.wav"
    (tmp_path / "config.toml").write_text('default_voice = "default"\n')
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["say", "hello", "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    fake.tts.assert_called_once()
    assert fake.tts.call_args.kwargs["speaker"] == "default"


def test_say_unknown_voice_routes_to_clone(runner, tmp_path):
    """`mimic say --voice <name>` for a non-builtin name should hit /clone/tts."""
    fake = MagicMock()
    fake.clone_tts.return_value = b"RIFF" + b"\x00" * 100
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)
    out = tmp_path / "out.wav"
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["say", "hello", "--voice", "jim", "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    fake.clone_tts.assert_called_once_with("jim", "hello", language="English")
    fake.tts.assert_not_called()
    assert out.exists()


def test_clone_say_with_out_writes_file(runner, tmp_path):
    fake = MagicMock()
    fake.clone_tts.return_value = b"RIFF" + b"\x00" * 100
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)
    out = tmp_path / "out.wav"
    with (
        patch("mimic.cli.Client", return_value=fake),
        patch("mimic.cli._play_wav_bytes") as play_fn,
    ):
        r = runner.invoke(app, ["clone", "say", "alice", "hello", "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    fake.clone_tts.assert_called_once_with("alice", "hello", language="English")
    assert out.exists()
    play_fn.assert_not_called()


def test_clone_say_without_out_plays_audio(runner):
    fake = MagicMock()
    wav_bytes = b"RIFF" + b"\x00" * 100
    fake.clone_tts.return_value = wav_bytes
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)
    with (
        patch("mimic.cli.Client", return_value=fake),
        patch("mimic.cli._play_wav_bytes") as play_fn,
    ):
        r = runner.invoke(app, ["clone", "say", "alice", "hello"])
    assert r.exit_code == 0, r.stdout
    play_fn.assert_called_once_with(wav_bytes)


def test_record_with_audio_and_text_skips_recorder(runner, tmp_path):
    audio = tmp_path / "ref.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)

    fake = MagicMock()
    fake.clone_register.return_value = {"status": "ok", "name": "alice"}
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=None)

    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(
            app,
            [
                "record",
                "alice",
                "--audio",
                str(audio),
                "--text",
                "transcript here",
            ],
        )
    assert r.exit_code == 0, r.stdout
    fake.clone_register.assert_called_once()


def test_config_prints_resolved_settings(runner, tmp_path):
    (tmp_path / "config.toml").write_text(
        'server_url = "http://nas.local:8000"\ndefault_voice = "Aiden"\n'
    )
    r = runner.invoke(app, ["config"])
    assert r.exit_code == 0
    assert "nas.local" in r.stdout
    assert "Aiden" in r.stdout


def test_whoami_prints_identity_and_quota(runner, monkeypatch):
    _stub_client(
        monkeypatch,
        whoami={
            "label": "dave",
            "role": "user",
            "can_upload": True,
            "max_voices": 5,
            "voices_used": 2,
            "daily_char_quota": 50000,
            "usage_today": {"requests": 3, "chars": 1200, "audio_seconds": 41.5},
        },
    )
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 0
    assert "dave" in result.stdout
    assert "1,200 / 50,000" in result.stdout
    assert "2 / 5" in result.stdout


def test_whoami_shows_unlimited_for_zero_quota(runner, monkeypatch):
    _stub_client(
        monkeypatch,
        whoami={
            "label": "root",
            "role": "admin",
            "can_upload": True,
            "max_voices": 5,
            "voices_used": 0,
            "daily_char_quota": 0,
            "usage_today": {"requests": 0, "chars": 0, "audio_seconds": 0.0},
        },
    )
    assert "unlimited" in runner.invoke(app, ["whoami"]).stdout


def test_clones_shows_owner_and_visibility(runner, monkeypatch):
    _stub_client(
        monkeypatch,
        clone_detail=[
            {
                "name": "warm",
                "qualified": "dave/warm",
                "owner": "dave",
                "visibility": "private",
                "mine": True,
            },
            {
                "name": "piper",
                "qualified": "jim/piper",
                "owner": "jim",
                "visibility": "public",
                "mine": False,
            },
        ],
    )
    out = runner.invoke(app, ["clones"]).stdout
    assert "dave/warm" in out
    assert "private" in out
    assert "jim/piper" in out
    assert "public" in out


def test_clones_mine_filters_to_owned(runner, monkeypatch):
    _stub_client(
        monkeypatch,
        clone_detail=[
            {
                "name": "warm",
                "qualified": "dave/warm",
                "owner": "dave",
                "visibility": "private",
                "mine": True,
            },
            {
                "name": "piper",
                "qualified": "jim/piper",
                "owner": "jim",
                "visibility": "public",
                "mine": False,
            },
        ],
    )
    out = runner.invoke(app, ["clones", "--mine"]).stdout
    assert "dave/warm" in out
    assert "jim/piper" not in out


def test_clones_falls_back_when_server_has_no_detail(runner, monkeypatch):
    _stub_client(monkeypatch, clone_detail=[], clones=["warm"])
    assert "warm" in runner.invoke(app, ["clones"]).stdout


def test_quota_error_is_a_clean_message_not_a_traceback(runner, monkeypatch):
    _stub_client(
        monkeypatch,
        say_raises=MimicQuotaError(
            429,
            "daily character quota exceeded (95/100)",
            used=95,
            limit=100,
            resets_at="2026-08-12T00:00:00+00:00",
        ),
    )
    result = runner.invoke(app, ["say", "hello"])
    assert result.exit_code == 1
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1
    assert "quota" in lines[0].lower()
    assert "95 / 100" in lines[0]
    assert "2026-08-12T00:00:00+00:00" in lines[0]
    assert "Traceback" not in result.output


def test_forbidden_error_is_a_clean_message(runner, monkeypatch):
    _stub_client(monkeypatch, say_raises=MimicForbiddenError(403, "admin key required"))
    result = runner.invoke(app, ["say", "hello"])
    assert result.exit_code == 1
    assert "admin key required" in result.stderr
    assert "Traceback" not in result.output


def test_404_not_found_error_is_one_clean_line(runner, monkeypatch):
    """A 404 must not imply the voice exists — it doesn't, and never did."""
    _stub_client(monkeypatch, say_raises=MimicNotFoundError(404, "clone voice 'ghost' not found"))
    result = runner.invoke(app, ["say", "hello"])
    assert result.exit_code == 1
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1
    assert "not found" in lines[0].lower()
    assert "ghost" in lines[0]
    assert "Traceback" not in result.output


def test_422_validation_error_formats_field_and_message(runner, monkeypatch):
    body = {
        "detail": [
            {
                "type": "greater_than_equal",
                "loc": ["body", "max_voices"],
                "msg": "Input should be greater than or equal to 0",
                "input": -1,
            }
        ]
    }
    _stub_client(
        monkeypatch,
        say_raises=MimicValidationError(422, str(body["detail"]), body=body),
    )
    result = runner.invoke(app, ["say", "hello"])
    assert result.exit_code == 1
    assert "max_voices" in result.stderr
    assert "Input should be greater than or equal to 0" in result.stderr
    assert "Traceback" not in result.output


def test_409_ambiguous_voice_shows_candidates(runner, monkeypatch):
    body = {"detail": "ambiguous voice name 'warm'", "candidates": ["dave/warm", "erin/warm"]}
    _stub_client(
        monkeypatch,
        say_raises=MimicValidationError(409, body["detail"], body=body),
    )
    result = runner.invoke(app, ["say", "hello"])
    assert result.exit_code == 1
    assert "dave/warm" in result.stderr
    assert "erin/warm" in result.stderr
    assert "Traceback" not in result.output


def test_409_ambiguous_voice_does_not_repeat_the_qualified_name_instruction(runner, monkeypatch):
    """The server's own message already says 'use a qualified name'; don't say it twice."""
    body = {
        "detail": "'warm' matches several voices; use a qualified name",
        "candidates": ["dave/warm", "erin/warm"],
    }
    _stub_client(
        monkeypatch,
        say_raises=MimicValidationError(409, body["detail"], body=body),
    )
    result = runner.invoke(app, ["say", "hello"])
    assert result.exit_code == 1
    assert result.stderr.lower().count("use a qualified name") == 1
    assert "dave/warm" in result.stderr
    assert "erin/warm" in result.stderr


def test_share_to_a_person_grants(runner, monkeypatch):
    stub = _stub_client(monkeypatch)
    result = runner.invoke(app, ["share", "warm", "--to", "dave"])
    assert result.exit_code == 0
    assert stub.calls == [("grant_voice", "warm", "dave")]
    assert "dave" in result.stdout


def test_share_public_sets_visibility(runner, monkeypatch):
    stub = _stub_client(monkeypatch)
    assert runner.invoke(app, ["share", "warm", "--public"]).exit_code == 0
    assert stub.calls == [("set_visibility", "warm", "public")]


def test_share_private_unpublishes(runner, monkeypatch):
    stub = _stub_client(monkeypatch)
    assert runner.invoke(app, ["share", "warm", "--private"]).exit_code == 0
    assert stub.calls == [("set_visibility", "warm", "private")]


def test_share_requires_exactly_one_target(runner, monkeypatch):
    _stub_client(monkeypatch)
    bare = runner.invoke(app, ["share", "warm"])
    assert bare.exit_code == 2
    assert "--to" in bare.stderr

    both = runner.invoke(app, ["share", "warm", "--to", "dave", "--public"])
    assert both.exit_code == 2


def test_share_public_and_private_together_is_rejected(runner, monkeypatch):
    _stub_client(monkeypatch)
    assert runner.invoke(app, ["share", "warm", "--public", "--private"]).exit_code == 2


def test_unshare_revokes(runner, monkeypatch):
    stub = _stub_client(monkeypatch)
    assert runner.invoke(app, ["unshare", "warm", "--from", "dave"]).exit_code == 0
    assert stub.calls == [("revoke_voice_grant", "warm", "dave")]
