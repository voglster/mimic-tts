import pytest
from mimic_server.db import Database
from mimic_server.errors import (
    AmbiguousVoice,
    Forbidden,
    UploadNotAllowed,
    VoiceLimitReached,
    VoiceNotFound,
)
from mimic_server.identity import Caller, KeyStore
from mimic_server.voices import VoiceRegistry


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "mimic.db")
    db.migrate()
    keys = KeyStore(db)
    registry = VoiceRegistry(db, keys, tmp_path / "reference")
    admin, _ = keys.create("root", role="admin")
    dave, _ = keys.create("dave")
    erin, _ = keys.create("erin")
    return registry, Caller(admin), Caller(dave), Caller(erin)


def _register(registry, caller, name):
    return registry.register(caller, name, b"RIFFfake", f"transcript for {name}")


def test_register_writes_audio_under_owner_namespace(env):
    registry, _, dave, _ = env
    voice = _register(registry, dave, "warm")
    assert voice.qualified == "dave/warm"
    assert (registry.dir_for("dave", "warm") / "audio.wav").read_bytes() == b"RIFFfake"
    assert registry.reference_paths(voice)[1] == "transcript for warm"


def test_same_name_different_owners_coexist(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    _register(registry, erin, "warm")
    assert registry.resolve(dave, "warm").owner_label == "dave"
    assert registry.resolve(erin, "warm").owner_label == "erin"


def test_private_voice_is_invisible_to_others(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    assert registry.visible_to(erin) == []
    with pytest.raises(VoiceNotFound):
        registry.resolve(erin, "dave/warm")


def test_admin_sees_and_resolves_every_voice(env):
    registry, admin, dave, _ = env
    _register(registry, dave, "warm")
    assert registry.resolve(admin, "dave/warm").owner_label == "dave"
    assert [v.qualified for v in registry.all_voices()] == ["dave/warm"]


def test_public_voice_is_resolvable_by_anyone(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    registry.set_visibility(dave, "warm", "public")
    assert registry.resolve(erin, "dave/warm").qualified == "dave/warm"


def test_grant_makes_a_private_voice_usable(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    registry.grant(dave, "warm", "erin")
    assert registry.resolve(erin, "dave/warm").qualified == "dave/warm"
    assert registry.grants_for(registry.resolve(dave, "warm")) == ["erin"]
    registry.revoke_grant(dave, "warm", "erin")
    with pytest.raises(VoiceNotFound):
        registry.resolve(erin, "dave/warm")


def test_admin_can_grant_someone_elses_voice(env):
    registry, admin, dave, erin = env
    _register(registry, dave, "warm")
    registry.grant(admin, "dave/warm", "erin")
    assert registry.resolve(erin, "dave/warm").qualified == "dave/warm"


def test_non_owner_cannot_grant_or_delete(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    registry.set_visibility(dave, "warm", "public")
    with pytest.raises(Forbidden):
        registry.grant(erin, "dave/warm", "erin")
    with pytest.raises(Forbidden):
        registry.delete(erin, "dave/warm")


def test_bare_name_prefers_own_voice_over_a_public_one(env):
    registry, _, dave, erin = env
    _register(registry, dave, "warm")
    registry.set_visibility(dave, "warm", "public")
    _register(registry, erin, "warm")
    assert registry.resolve(erin, "warm").owner_label == "erin"


def test_bare_name_is_ambiguous_across_two_public_voices(env):
    registry, admin, dave, erin = env
    _register(registry, dave, "warm")
    _register(registry, erin, "warm")
    registry.set_visibility(dave, "warm", "public")
    registry.set_visibility(erin, "warm", "public")
    with pytest.raises(AmbiguousVoice) as exc:
        registry.resolve(admin, "warm")
    assert exc.value.extra["candidates"] == ["dave/warm", "erin/warm"]


def test_delete_removes_row_and_files(env):
    registry, _, dave, _ = env
    _register(registry, dave, "warm")
    path = registry.dir_for("dave", "warm")
    registry.delete(dave, "warm")
    assert not path.exists()
    assert registry.visible_to(dave) == []
    with pytest.raises(VoiceNotFound):
        registry.resolve(dave, "warm")


def test_re_registering_same_name_replaces_in_place(env):
    registry, _, dave, _ = env
    first = _register(registry, dave, "warm")
    registry.register(dave, "warm", b"RIFFnew", "new transcript")
    assert registry.count_owned(dave.id) == 1
    assert registry.resolve(dave, "warm").id == first.id
    assert (registry.dir_for("dave", "warm") / "audio.wav").read_bytes() == b"RIFFnew"


def test_max_voices_enforced(env):
    registry, _, dave, _ = env
    registry.keys.update("dave", max_voices=1)
    _register(registry, dave, "one")
    with pytest.raises(VoiceLimitReached):
        _register(registry, dave, "two")


def test_upload_forbidden_when_can_upload_false(env):
    registry, _, dave, _ = env
    registry.keys.update("dave", can_upload=False)
    with pytest.raises(UploadNotAllowed):
        _register(registry, dave, "warm")


@pytest.mark.parametrize("bad", ["../evil", "a/b", "", ".", "..", "has space", "x" * 65])
def test_invalid_names_rejected(env, bad):
    registry, _, dave, _ = env
    with pytest.raises(ValueError, match="invalid voice name"):
        registry.register(dave, bad, b"RIFF", "t")
