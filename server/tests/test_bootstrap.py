from __future__ import annotations

import shutil
from pathlib import Path

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


def test_voice_sharing_root_label_does_not_sweep_sibling_voice(tmp_path):
    """Regression for the sweep bug: when root_label collides with a legacy
    voice's own name, an already-migrated sibling voice must not get pulled
    into the collision voice's destination and deleted from its real home."""
    reference = tmp_path / "reference"
    for name in ("alice", "jim"):
        legacy = reference / name
        legacy.mkdir(parents=True)
        (legacy / "audio.wav").write_bytes(name.encode() + b"-AUDIO")
        (legacy / "text.txt").write_text(f"hello from {name}")

    result = bootstrap(_settings(tmp_path, api_token="s3cret", root_label="jim"))  # noqa: S106

    assert sorted(v.qualified for v in result.voices.all_voices()) == ["jim/alice", "jim/jim"]
    assert (reference / "jim" / "alice" / "audio.wav").read_bytes() == b"alice-AUDIO"
    assert (reference / "jim" / "jim" / "audio.wav").read_bytes() == b"jim-AUDIO"


def test_differing_flat_file_reappearing_does_not_disturb_migrated_owner_dir(tmp_path):
    """Regression for the owner-dir quarantine bug: once `jim` and `piper`
    are migrated under reference/jim/, a *new* flat reference/jim/audio.wav
    with different content (e.g. someone re-recording through the old flat
    write path) must be quarantined without touching the owner directory or
    its already-migrated voices."""
    reference = tmp_path / "reference"
    for name in ("jim", "piper"):
        legacy = reference / name
        legacy.mkdir(parents=True)
        (legacy / "audio.wav").write_bytes(name.encode() + b"-AUDIO")
        (legacy / "text.txt").write_text(f"hello from {name}")

    settings = _settings(tmp_path, api_token="s3cret", root_label="jim")  # noqa: S106
    bootstrap(settings)

    (reference / "jim" / "audio.wav").write_bytes(b"NEW-DIFFERENT-AUDIO")
    (reference / "jim" / "text.txt").write_text("re-recorded")

    result = bootstrap(settings)

    assert (reference / "jim" / "jim").is_dir()
    assert (reference / "jim" / "jim" / "audio.wav").read_bytes() == b"jim-AUDIO"
    assert (reference / "jim" / "piper").is_dir()
    assert (reference / "jim" / "piper" / "audio.wav").read_bytes() == b"piper-AUDIO"
    conflict = reference / ".conflict-jim"
    assert conflict.is_dir()
    assert (conflict / "audio.wav").read_bytes() == b"NEW-DIFFERENT-AUDIO"
    for voice in result.voices.all_voices():
        wav_path, _ = result.voices.reference_paths(voice)
        assert wav_path.exists()


def test_dot_prefixed_dir_produces_no_warning(tmp_path, caplog):
    reference = tmp_path / "reference"
    conflict_lookalike = reference / ".conflict-piper"
    conflict_lookalike.mkdir(parents=True)
    (conflict_lookalike / "audio.wav").write_bytes(b"RIFF")

    with caplog.at_level("WARNING"):
        bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    assert caplog.text == ""


def test_reappearing_flat_dir_with_corrected_transcript_is_quarantined_not_deleted(
    tmp_path, caplog
):
    """Regression: a duplicate decision based on audio.wav bytes alone
    deleted the rest of the payload (transcript, notes) whenever a flat
    voice reappeared with the same audio but a corrected text.txt."""
    reference = tmp_path / "reference"
    destination = reference / "root" / "piper"
    destination.mkdir(parents=True)
    (destination / "audio.wav").write_bytes(b"RIFF")
    (destination / "text.txt").write_text("original transcript")

    settings = _settings(tmp_path, api_token="s3cret")  # noqa: S106
    bootstrap(settings)

    legacy = reference / "piper"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"RIFF")  # identical audio bytes
    (legacy / "text.txt").write_text("CORRECTED TRANSCRIPT")
    (legacy / "notes.md").write_text("IRREPLACEABLE NOTES")

    with caplog.at_level("INFO"):
        result = bootstrap(settings)

    assert (destination / "text.txt").read_text() == "original transcript"
    conflict = reference / ".conflict-piper"
    assert conflict.is_dir()
    assert (conflict / "text.txt").read_text() == "CORRECTED TRANSCRIPT"
    assert (conflict / "notes.md").read_text() == "IRREPLACEABLE NOTES"
    assert [v.qualified for v in result.voices.all_voices()] == ["root/piper"]
    assert caplog.text


def test_evacuation_moves_audio_last(tmp_path, monkeypatch):
    """Regression: evacuating payload in raw iterdir() order could move
    audio.wav before other files. A crash in between would leave the
    remaining files unreachable forever, since the top-level gate that
    finds legacy voices only looks for a top-level audio.wav. Forces
    iterdir() to yield audio.wav first -- the worst case -- so the
    assertion doesn't depend on incidental filesystem ordering."""
    reference = tmp_path / "reference"
    legacy = reference / "piper"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"RIFF")
    (legacy / "text.txt").write_text("t")
    (legacy / "notes.md").write_text("extra")

    real_iterdir = Path.iterdir

    def audio_first_iterdir(self):
        entries = list(real_iterdir(self))
        if self == legacy:
            entries.sort(key=lambda p: p.name != "audio.wav")
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", audio_first_iterdir)

    move_order = []
    real_move = shutil.move

    def recording_move(src, dst):
        move_order.append(Path(src).name)
        return real_move(src, dst)

    monkeypatch.setattr("mimic_server.bootstrap.shutil.move", recording_move)

    bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    evacuation_moves = [n for n in move_order if n in {"audio.wav", "text.txt", "notes.md"}]
    assert set(evacuation_moves) == {"audio.wav", "text.txt", "notes.md"}
    assert evacuation_moves[-1] == "audio.wav"


def test_resumes_when_only_audio_remains_to_be_evacuated(tmp_path):
    """Companion to the ordering fix: if a crash happened right before the
    last (audio.wav) move, the already-staged files and the still-present
    source must merge cleanly into a fully resolvable voice."""
    reference = tmp_path / "reference"
    legacy = reference / "piper"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"RIFF")

    staged = reference / ".migrate-staging" / "piper"
    staged.mkdir(parents=True)
    (staged / "text.txt").write_text("t")
    (staged / "notes.md").write_text("extra")

    result = bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    assert [v.qualified for v in result.voices.all_voices()] == ["root/piper"]
    voice = result.voices.all_voices()[0]
    wav_path, text = result.voices.reference_paths(voice)
    assert wav_path.read_bytes() == b"RIFF"
    assert text == "t"
    assert (reference / "root" / "piper" / "notes.md").read_text() == "extra"


def test_rotating_the_env_token_invalidates_the_old_one(tmp_path):
    bootstrap(_settings(tmp_path, api_token="old"))  # noqa: S106
    result = bootstrap(_settings(tmp_path, api_token="new"))  # noqa: S106
    assert result.keys.authenticate("old") is None
    assert result.keys.authenticate("new").is_admin


def test_invalid_root_label_fails_loudly_before_touching_the_filesystem(tmp_path):
    with pytest.raises(ValueError, match="MIMIC_ROOT_LABEL"):
        bootstrap(_settings(tmp_path, api_token="s3cret", root_label="bad/label"))  # noqa: S106

    assert not (tmp_path / "mimic.db").exists()


def test_evacuation_never_overwrites_a_staged_file(tmp_path):
    """Crash mid-evacuation, restore the flat dir from backup, reboot.

    Both transcripts must survive: the staged one is already in the pipeline,
    the incoming one is what the operator just restored.
    """
    reference = tmp_path / "reference"
    legacy = reference / "piper"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(b"RIFFincoming")
    (legacy / "text.txt").write_text("INCOMING TRANSCRIPT")

    staged = reference / ".migrate-staging" / "piper"
    staged.mkdir(parents=True)
    (staged / "text.txt").write_text("STAGED TRANSCRIPT")

    bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    installed = reference / "root" / "piper"
    assert installed.joinpath("text.txt").read_text() == "STAGED TRANSCRIPT"

    quarantined = reference / ".conflict-piper-evacuated" / "text.txt"
    assert quarantined.read_text() == "INCOMING TRANSCRIPT"


def test_dir_without_audio_is_left_alone_and_logged(tmp_path, caplog):
    reference = tmp_path / "reference"
    orphan = reference / "halfrecorded"
    orphan.mkdir(parents=True)
    (orphan / "text.txt").write_text("transcript but no audio")

    with caplog.at_level("WARNING"):
        result = bootstrap(_settings(tmp_path, api_token="s3cret"))  # noqa: S106

    assert result.voices.all_voices() == []
    assert (orphan / "text.txt").read_text() == "transcript but no audio"
    assert "halfrecorded" in caplog.text
