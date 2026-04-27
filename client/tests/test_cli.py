from unittest.mock import MagicMock, patch

import pytest
from mimic.cli import app
from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMIC_SERVER_URL", raising=False)
    monkeypatch.delenv("MIMIC_API_TOKEN", raising=False)
    monkeypatch.setenv("MIMIC_CONFIG_DIR", str(tmp_path))


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
    (tmp_path / "config.toml").write_text('default_voice = "Aiden"\n')
    with patch("mimic.cli.Client", return_value=fake):
        r = runner.invoke(app, ["say", "hello", "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    fake.tts.assert_called_once()
    assert fake.tts.call_args.kwargs["speaker"] == "Aiden"


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
