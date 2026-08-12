"""Synthesis choke point: quota, resolution, and usage recording across all
synth-triggering routes (/tts, /clone/tts, /v1/audio/speech, /clone/oneshot)."""

from __future__ import annotations

from conftest import _auth, _register, _wav
from mimic_server.usage import UsageTracker

# `env` and `fake_backend` are autoloaded pytest fixtures from
# tests/conftest.py.


def test_builtin_tts_records_usage(env):
    client, tokens, _ = env
    assert (
        client.post("/tts", headers=_auth(tokens, "dave"), data={"text": "hello there"}).status_code
        == 200
    )
    r = client.get("/me", headers=_auth(tokens, "dave"))
    assert r.json()["usage_today"]["chars"] == len("hello there")


def test_builtin_tts_records_usage_via_tracker(env):
    client, tokens, keys = env
    assert (
        client.post("/tts", headers=_auth(tokens, "dave"), data={"text": "hello there"}).status_code
        == 200
    )
    dave_key = keys.get_by_label("dave")
    usage = UsageTracker(keys.db)
    assert usage.chars_today(dave_key.id) == len("hello there")


def test_clone_tts_with_own_voice(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.post(
        "/clone/tts", headers=_auth(tokens, "dave"), data={"text": "hi", "name": "warm"}
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"


def test_clone_tts_with_someone_elses_private_voice_is_404(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.post(
        "/clone/tts", headers=_auth(tokens, "erin"), data={"text": "hi", "name": "dave/warm"}
    )
    assert r.status_code == 404


def test_clone_tts_works_after_a_grant(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    client.post(
        "/clone/voices/warm/grants", headers=_auth(tokens, "dave"), json={"grantee": "erin"}
    )
    r = client.post(
        "/clone/tts", headers=_auth(tokens, "erin"), data={"text": "hi", "name": "dave/warm"}
    )
    assert r.status_code == 200


def test_ambiguous_bare_name_is_409(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    _register(client, tokens, "erin", "warm")
    for who in ("dave", "erin"):
        client.patch(
            f"/clone/voices/{who}/warm",
            headers=_auth(tokens, "root"),
            json={"visibility": "public"},
        )
    r = client.post(
        "/clone/tts", headers=_auth(tokens, "root"), data={"text": "hi", "name": "warm"}
    )
    assert r.status_code == 409
    assert r.json()["candidates"] == ["dave/warm", "erin/warm"]


def test_quota_exceeded_is_429_and_blocks_synthesis(env):
    client, tokens, keys = env
    keys.update("dave", daily_char_quota=5)
    r = client.post("/tts", headers=_auth(tokens, "dave"), data={"text": "way too long"})
    assert r.status_code == 429
    body = r.json()
    assert body["error"] == "quota_exceeded"
    assert body["limit"] == 5
    assert "resets_at" in body


def test_admin_ignores_quota(env):
    client, tokens, keys = env
    keys.update("root", daily_char_quota=1)
    assert (
        client.post(
            "/tts", headers=_auth(tokens, "root"), data={"text": "way too long"}
        ).status_code
        == 200
    )


def test_openai_endpoint_honors_permissions(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    ok = client.post(
        "/v1/audio/speech",
        headers=_auth(tokens, "dave"),
        json={"input": "hi", "voice": "warm", "response_format": "wav"},
    )
    assert ok.status_code == 200
    denied = client.post(
        "/v1/audio/speech",
        headers=_auth(tokens, "erin"),
        json={"input": "hi", "voice": "dave/warm", "response_format": "wav"},
    )
    assert denied.status_code == 404


def test_oneshot_counts_against_quota(env):
    client, tokens, keys = env
    keys.update("dave", daily_char_quota=3)
    r = client.post(
        "/clone/oneshot",
        headers=_auth(tokens, "dave"),
        data={"text": "much longer than three", "ref_text": "hello"},
        files={"ref_audio": ("a.wav", _wav(), "audio/wav")},
    )
    assert r.status_code == 429


def test_clone_tts_prefers_a_same_named_clone_over_a_builtin(env, fake_backend, tmp_path):
    """A voice named "default" must be reachable on /clone/tts even though
    "default" is also a built-in voice name — /clone/tts is the clone
    endpoint, so the registry must win there. `/clone/register` now refuses
    to create new voices with that name (see the dedicated 400 test below),
    so this simulates a pre-existing (e.g. bootstrap-adopted) voice by
    registering directly through `VoiceRegistry`, bypassing the HTTP route.
    `tmp_path` is the same directory `env` built its `Settings` from —
    pytest hands out one `tmp_path` per test, shared across all fixtures a
    given test requests."""
    client, tokens, keys = env
    from mimic_server.identity import Caller
    from mimic_server.voices import VoiceRegistry

    voices = VoiceRegistry(keys.db, keys, tmp_path / "reference")
    voices.register(Caller(key=keys.get_by_label("dave")), "default", _wav(), "hello")

    r = client.post(
        "/clone/tts", headers=_auth(tokens, "dave"), data={"text": "hi", "name": "default"}
    )
    assert r.status_code == 200
    fake_backend.synth_clone.assert_called_once()
    fake_backend.synth_builtin.assert_not_called()


def test_tts_still_prefers_the_builtin_named_default(env, fake_backend):
    client, tokens, _ = env
    _register(client, tokens, "dave", "default")
    r = client.post(
        "/tts", headers=_auth(tokens, "dave"), data={"text": "hi", "speaker": "default"}
    )
    assert r.status_code == 200
    fake_backend.synth_builtin.assert_called_once()
    fake_backend.synth_clone.assert_not_called()


def test_openai_speech_still_prefers_the_builtin_named_default(env, fake_backend):
    client, tokens, _ = env
    _register(client, tokens, "dave", "default")
    r = client.post(
        "/v1/audio/speech",
        headers=_auth(tokens, "dave"),
        json={"input": "hi", "voice": "default", "response_format": "wav"},
    )
    assert r.status_code == 200
    fake_backend.synth_builtin.assert_called_once()
    fake_backend.synth_clone.assert_not_called()


def test_register_rejects_a_name_that_collides_with_a_builtin_voice(env):
    client, tokens, _ = env
    r = _register(client, tokens, "dave", "default")
    assert r.status_code == 400
    assert r.json()["error"] == "reserved_name"


def test_instruct_on_a_resolved_clone_voice_is_400(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.post(
        "/tts",
        headers=_auth(tokens, "dave"),
        data={"text": "hi", "speaker": "warm", "instruct": "whisper it"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"
