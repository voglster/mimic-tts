from __future__ import annotations

import pytest
from mimic_server.bootstrap import bootstrap
from mimic_server.config import Settings


def _settings(tmp_path, **kw):
    return Settings(
        reference_dir=tmp_path / "reference",
        db_path=tmp_path / "mimic.db",
        **kw,
    )


def test_creates_root_key_from_env_token(tmp_path):
    result = bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106
    assert result.root.label == "root"
    assert result.root.is_admin
    assert result.root.managed_by_env
    assert result.keys.authenticate("s3cret").id == result.root.id


def test_root_label_is_configurable(tmp_path):
    result = bootstrap(_settings(tmp_path, api_token="s3cret", root_label="jim"))  # noqa: S106
    assert result.root.label == "jim"


def test_dev_mode_still_gets_a_root_key(tmp_path):
    result = bootstrap(_settings(tmp_path))
    assert result.root.is_admin


def test_adopts_and_moves_legacy_flat_voices(tmp_path):
    reference = tmp_path / "reference"
    for name in ("jim", "piper"):
        legacy = reference / name
        legacy.mkdir(parents=True)
        (legacy / "audio.wav").write_bytes(b"RIFF" + name.encode())
        (legacy / "text.txt").write_text(f"hello from {name}")

    result = bootstrap(_settings(tmp_path, api_token="s3cret", root_label="jim"))  # noqa: S106

    assert sorted(v.qualified for v in result.voices.all_voices()) == ["jim/jim", "jim/piper"]
    moved = reference / "jim" / "piper" / "audio.wav"
    assert moved.read_bytes() == b"RIFFpiper"
    assert not (reference / "piper").exists()


def test_bootstrap_is_idempotent(tmp_path):
    reference = tmp_path / "reference"
    legacy = reference / "piper"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"RIFF")
    (legacy / "text.txt").write_text("t")

    settings = _settings(tmp_path, api_token="s3cret")  # noqa: S106
    bootstrap(settings)
    second = bootstrap(settings)

    assert [v.qualified for v in second.voices.all_voices()] == ["root/piper"]
    assert sorted(p.name for p in reference.iterdir()) == ["root"]
    assert sorted(p.name for p in (reference / "root").iterdir()) == ["piper"]


def test_reconciles_orphaned_voice_missing_db_row(tmp_path):
    """Simulates a crash between the filesystem move and the DB write: the
    voice's files already live in their final home but no row exists yet."""
    reference = tmp_path / "reference"
    voice_dir = reference / "root" / "piper"
    voice_dir.mkdir(parents=True)
    (voice_dir / "audio.wav").write_bytes(b"RIFF")
    (voice_dir / "text.txt").write_text("t")

    result = bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    assert [v.qualified for v in result.voices.all_voices()] == ["root/piper"]


def test_invalid_legacy_name_is_skipped_not_deleted(tmp_path, caplog):
    reference = tmp_path / "reference"
    bad = reference / "bad name!"
    bad.mkdir(parents=True)
    (bad / "audio.wav").write_bytes(b"RIFF")
    (bad / "text.txt").write_text("t")

    with caplog.at_level("WARNING"):
        result = bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    assert bad.exists()
    assert (bad / "audio.wav").read_bytes() == b"RIFF"
    assert result.voices.all_voices() == []
    assert "bad name!" in caplog.text


def test_conflicting_destination_preserves_both_copies(tmp_path):
    reference = tmp_path / "reference"
    legacy = reference / "piper"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"LEGACY-IRREPLACEABLE")
    (legacy / "text.txt").write_text("legacy text")

    destination = reference / "root" / "piper"
    destination.mkdir(parents=True)
    (destination / "audio.wav").write_bytes(b"DIFFERENT-CONTENT")
    (destination / "text.txt").write_text("dest text")

    result = bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    assert (destination / "audio.wav").read_bytes() == b"DIFFERENT-CONTENT"
    conflict = reference / ".conflict-piper"
    assert conflict.is_dir()
    assert (conflict / "audio.wav").read_bytes() == b"LEGACY-IRREPLACEABLE"
    assert not legacy.exists()
    assert [v.qualified for v in result.voices.all_voices()] == ["root/piper"]


def test_incomplete_destination_is_quarantined_not_destroyed(tmp_path):
    reference = tmp_path / "reference"
    legacy = reference / "piper"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"RIFF")
    (legacy / "text.txt").write_text("t")

    destination = reference / "root" / "piper"
    destination.mkdir(parents=True)
    (destination / "notes.txt").write_text("unrelated planted file")

    result = bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    conflict = reference / ".conflict-piper"
    assert conflict.is_dir()
    assert (conflict / "notes.txt").read_text() == "unrelated planted file"
    assert (destination / "audio.wav").read_bytes() == b"RIFF"
    assert [v.qualified for v in result.voices.all_voices()] == ["root/piper"]


def test_extra_files_are_carried_over(tmp_path):
    reference = tmp_path / "reference"
    legacy = reference / "piper"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"RIFF")
    (legacy / "text.txt").write_text("t")
    (legacy / "notes.md").write_text("extra metadata")

    bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    assert (reference / "root" / "piper" / "notes.md").read_text() == "extra metadata"


def test_resumes_after_partial_run_leaving_destination_and_source(tmp_path):
    """Simulates a crash after the copy+move but before the original was
    cleaned up: both the legacy source and the fully-populated destination
    exist with identical content."""
    reference = tmp_path / "reference"
    legacy = reference / "piper"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"RIFF")
    (legacy / "text.txt").write_text("t")

    destination = reference / "root" / "piper"
    destination.mkdir(parents=True)
    (destination / "audio.wav").write_bytes(b"RIFF")
    (destination / "text.txt").write_text("t")

    result = bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    assert [v.qualified for v in result.voices.all_voices()] == ["root/piper"]
    assert not legacy.exists()
    assert (destination / "audio.wav").read_bytes() == b"RIFF"


def test_rotating_the_env_token_invalidates_the_old_one(tmp_path):
    bootstrap(_settings(tmp_path, api_token="old"))  # noqa: S106
    result = bootstrap(_settings(tmp_path, api_token="new"))  # noqa: S106
    assert result.keys.authenticate("old") is None
    assert result.keys.authenticate("new").is_admin


def test_invalid_root_label_fails_loudly_before_touching_the_filesystem(tmp_path):
    with pytest.raises(ValueError, match="MIMIC_ROOT_LABEL"):
        bootstrap(_settings(tmp_path, api_token="s3cret", root_label="bad/label"))  # noqa: S106

    assert not (tmp_path / "mimic.db").exists()
