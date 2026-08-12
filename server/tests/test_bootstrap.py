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


def test_rotating_the_env_token_invalidates_the_old_one(tmp_path):
    bootstrap(_settings(tmp_path, api_token="old"))  # noqa: S106
    result = bootstrap(_settings(tmp_path, api_token="new"))  # noqa: S106
    assert result.keys.authenticate("old") is None
    assert result.keys.authenticate("new").is_admin


def test_invalid_root_label_fails_loudly_before_touching_the_filesystem(tmp_path):
    with pytest.raises(ValueError, match="MIMIC_ROOT_LABEL"):
        bootstrap(_settings(tmp_path, api_token="s3cret", root_label="bad/label"))  # noqa: S106

    assert not (tmp_path / "mimic.db").exists()
