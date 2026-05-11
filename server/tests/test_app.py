from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mimic_server.app import build_app
from mimic_server.config import Settings


@pytest.fixture
def fake_backend():
    b = MagicMock()
    audio = np.zeros(1024, dtype=np.float32)
    b.builtin_voices.return_value = [{"name": "default", "language": "English"}]
    b.synth_builtin.return_value = (audio, 24000)
    b.synth_clone.return_value = (audio, 24000)
    b.synth_clone_oneshot.return_value = (audio, 24000)
    b.loaded_keys.return_value = []

    async def _no_lifecycle():
        return None

    b.run_lifecycle = _no_lifecycle
    return b


def _app(tmp_path, fake_backend, *, token=None):
    settings = Settings(reference_dir=tmp_path, api_token=token)
    return build_app(settings, backend_factory=lambda _s: fake_backend)


def test_health_no_auth(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["backend"] == "chatterbox"


def test_voices_unauthenticated_when_no_token(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    assert client.get("/voices").status_code == 200


def test_protected_route_rejects_without_token(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend, token="shhh"))  # noqa: S106
    assert client.get("/voices").status_code == 401


def test_health_remains_open_even_with_token(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend, token="shhh"))  # noqa: S106
    assert client.get("/health").status_code == 200


def test_tts_endpoint_returns_wav(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/tts", data={"text": "hello", "speaker": "default"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"


def test_tts_unsupported_speaker_returns_400(tmp_path, fake_backend):
    """Backends surface unsupported built-in speakers as HTTP 400."""
    fake_backend.synth_builtin.side_effect = HTTPException(
        status_code=400, detail="No built-in voice 'jim'."
    )
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/tts", data={"text": "hi", "speaker": "jim"})
    assert r.status_code == 400
    assert "jim" in r.json()["detail"]


def test_clone_register_writes_files_and_lists_voice(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post(
        "/clone/register",
        data={"ref_text": "hi there", "name": "alice"},
        files={"ref_audio": ("a.wav", b"\x00\x01\x02", "audio/wav")},
    )
    assert r.status_code == 200
    assert (tmp_path / "alice" / "audio.wav").read_bytes() == b"\x00\x01\x02"
    assert (tmp_path / "alice" / "text.txt").read_text() == "hi there"
    r = client.get("/clone/voices")
    assert r.json() == {"voices": ["alice"]}


def test_clone_tts_unknown_voice_returns_400(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/clone/tts", data={"text": "hi", "name": "nobody"})
    assert r.status_code == 400


def test_clone_tts_uses_backend(tmp_path, fake_backend):
    # Pre-register on disk
    voice_dir = tmp_path / "alice"
    voice_dir.mkdir()
    (voice_dir / "audio.wav").write_bytes(b"\x00")
    (voice_dir / "text.txt").write_text("hi there")

    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/clone/tts", data={"text": "hello", "name": "alice"})
    assert r.status_code == 200
    fake_backend.synth_clone.assert_called_once()
    kwargs = fake_backend.synth_clone.call_args.kwargs
    assert kwargs["name"] == "alice"
    assert kwargs["ref_text"] == "hi there"


def test_openai_speech_routes_builtin_voice(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hello", "voice": "default"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    fake_backend.synth_builtin.assert_called_once()
    fake_backend.synth_clone.assert_not_called()


def test_openai_speech_routes_clone_voice(tmp_path, fake_backend):
    voice_dir = tmp_path / "piper"
    voice_dir.mkdir()
    (voice_dir / "audio.wav").write_bytes(b"\x00")
    (voice_dir / "text.txt").write_text("sample")
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hi", "voice": "piper"},
    )
    assert r.status_code == 200
    fake_backend.synth_clone.assert_called_once()
    fake_backend.synth_builtin.assert_not_called()


def test_openai_speech_unknown_voice_returns_400(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hi", "voice": "nobody"},
    )
    assert r.status_code == 400


def test_openai_speech_rejects_mp3_with_helpful_message(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post(
        "/v1/audio/speech",
        json={
            "model": "tts-1",
            "input": "hi",
            "voice": "default",
            "response_format": "mp3",
        },
    )
    assert r.status_code == 400
    assert "mp3" in r.json()["detail"].lower()


def test_openai_speech_flac_returns_audio_flac(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post(
        "/v1/audio/speech",
        json={
            "model": "tts-1",
            "input": "hi",
            "voice": "default",
            "response_format": "flac",
        },
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/flac"


def test_openai_speech_requires_auth_when_token_set(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend, token="shhh"))  # noqa: S106
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hi", "voice": "default"},
    )
    assert r.status_code == 401


def test_public_bind_without_token_refuses_to_start(tmp_path, fake_backend):
    settings = Settings(reference_dir=tmp_path, host="0.0.0.0", api_token=None)
    with pytest.raises(RuntimeError, match="MIMIC_API_TOKEN"):
        build_app(settings, backend_factory=lambda _s: fake_backend)


def test_public_bind_with_explicit_override_starts(tmp_path, fake_backend):
    settings = Settings(
        reference_dir=tmp_path,
        host="0.0.0.0",
        api_token=None,
        allow_unauthenticated_public_bind=True,
    )
    app = build_app(settings, backend_factory=lambda _s: fake_backend)
    assert app is not None  # smoke — no RuntimeError
