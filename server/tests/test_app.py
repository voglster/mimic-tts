import io
from unittest.mock import MagicMock

import numpy as np
import pytest
import soundfile as sf
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mimic_server.app import build_app
from mimic_server.config import Settings


def _silent_wav_bytes(duration_s: float = 0.5, sample_rate: int = 24000) -> bytes:
    """A real WAV blob ffmpeg will accept — used by tests that exercise the
    clone-register / clone-oneshot upload path now that we transcode."""
    samples = np.zeros(int(duration_s * sample_rate), dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


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
    settings = Settings(reference_dir=tmp_path, db_path=tmp_path / "mimic.db", api_token=token)
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


def test_tts_unsupported_builtin_speaker_returns_400(tmp_path, fake_backend):
    """Backends surface unsupported built-in speakers as HTTP 400."""
    fake_backend.synth_builtin.side_effect = HTTPException(
        status_code=400, detail="No built-in voice 'default'."
    )
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/tts", data={"text": "hi", "speaker": "default"})
    assert r.status_code == 400
    assert "default" in r.json()["detail"]


def test_tts_unknown_speaker_that_is_not_a_voice_returns_404(tmp_path, fake_backend):
    """`/tts` now resolves non-built-in speakers as clone voices via the shared
    `synthesize()` choke point, so an unrecognized name is a 404, not a
    backend-level 400."""
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/tts", data={"text": "hi", "speaker": "jim"})
    assert r.status_code == 404


def test_clone_register_writes_files_and_lists_voice(tmp_path, fake_backend):
    # No bearer token configured -> every request authenticates as the local
    # root admin, so an unqualified register lands under "root/<name>".
    client = TestClient(_app(tmp_path, fake_backend))
    wav = _silent_wav_bytes()
    r = client.post(
        "/clone/register",
        data={"ref_text": "hi there", "name": "alice"},
        files={"ref_audio": ("a.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "root/alice"
    # Stored file is now ffmpeg's re-muxed WAV — verify it's a readable WAV
    # rather than asserting byte-equality with the input.
    stored = (tmp_path / "root" / "alice" / "audio.wav").read_bytes()
    assert stored.startswith(b"RIFF")
    assert b"WAVE" in stored[:12]
    assert (tmp_path / "root" / "alice" / "text.txt").read_text() == "hi there"
    r = client.get("/clone/voices")
    assert r.json()["voices"] == ["root/alice"]


def test_clone_register_rejects_undecodable_audio(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post(
        "/clone/register",
        data={"ref_text": "hi", "name": "alice"},
        files={"ref_audio": ("garbage.bin", b"not audio at all", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "decode" in r.json()["detail"].lower() or "ffmpeg" in r.json()["detail"].lower()


def test_clone_delete_removes_voice(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    wav = _silent_wav_bytes()
    client.post(
        "/clone/register",
        data={"ref_text": "hi there", "name": "alice"},
        files={"ref_audio": ("a.wav", wav, "audio/wav")},
    )
    voice_dir = tmp_path / "root" / "alice"
    assert voice_dir.is_dir()

    r = client.delete("/clone/voices/alice")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert not voice_dir.exists()


def test_clone_delete_unknown_returns_404(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.delete("/clone/voices/nobody")
    assert r.status_code == 404


def test_stt_disabled_returns_503(tmp_path, fake_backend):
    # Default settings have stt_uri unset → /stt should refuse.
    client = TestClient(_app(tmp_path, fake_backend))
    wav = _silent_wav_bytes()
    r = client.post("/stt", files={"audio": ("clip.wav", wav, "audio/wav")})
    assert r.status_code == 503


def test_health_reports_stt_flag(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.get("/health")
    assert r.json()["stt_enabled"] is False


def test_tts_default_returns_wav(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/tts", data={"text": "hi"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/wav")


def test_tts_format_mp3_routes_through_ffmpeg(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/tts", data={"text": "hi", "format": "mp3"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    # MP3 frame headers start with 0xFFFB / 0xFFF3 / 0xFFFA / etc. (sync word).
    # Just verify the body is non-empty and doesn't look like a WAV RIFF.
    assert len(r.content) > 0
    assert not r.content.startswith(b"RIFF")


def test_tts_format_opus_returns_ogg(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/tts", data={"text": "hi", "format": "opus"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/ogg"
    assert r.content.startswith(b"OggS")


def test_tts_unknown_format_returns_400(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/tts", data={"text": "hi", "format": "made-up"})
    assert r.status_code == 400
    assert "format" in r.json()["detail"].lower()


def test_clone_tts_unknown_voice_returns_404(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post("/clone/tts", data={"text": "hi", "name": "nobody"})
    assert r.status_code == 404


def test_clone_tts_uses_backend(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    wav = _silent_wav_bytes()
    client.post(
        "/clone/register",
        data={"ref_text": "hi there", "name": "alice"},
        files={"ref_audio": ("a.wav", wav, "audio/wav")},
    )

    r = client.post("/clone/tts", data={"text": "hello", "name": "alice"})
    assert r.status_code == 200
    fake_backend.synth_clone.assert_called_once()
    kwargs = fake_backend.synth_clone.call_args.kwargs
    assert kwargs["name"] == "root/alice"
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
    client = TestClient(_app(tmp_path, fake_backend))
    wav = _silent_wav_bytes()
    client.post(
        "/clone/register",
        data={"ref_text": "sample", "name": "piper"},
        files={"ref_audio": ("a.wav", wav, "audio/wav")},
    )
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hi", "voice": "piper"},
    )
    assert r.status_code == 200
    fake_backend.synth_clone.assert_called_once()
    fake_backend.synth_builtin.assert_not_called()
    assert fake_backend.synth_clone.call_args.kwargs["name"] == "root/piper"


def test_openai_speech_unknown_voice_returns_404(tmp_path, fake_backend):
    client = TestClient(_app(tmp_path, fake_backend))
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hi", "voice": "nobody"},
    )
    assert r.status_code == 404


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
    settings = Settings(
        reference_dir=tmp_path, db_path=tmp_path / "mimic.db", host="0.0.0.0", api_token=None
    )
    with pytest.raises(RuntimeError, match="MIMIC_API_TOKEN"):
        build_app(settings, backend_factory=lambda _s: fake_backend)


def test_public_bind_with_explicit_override_starts(tmp_path, fake_backend):
    settings = Settings(
        reference_dir=tmp_path,
        db_path=tmp_path / "mimic.db",
        host="0.0.0.0",
        api_token=None,
        allow_unauthenticated_public_bind=True,
    )
    app = build_app(settings, backend_factory=lambda _s: fake_backend)
    assert app is not None  # smoke — no RuntimeError


def test_public_bind_override_resolves_anonymous_callers_to_non_admin(tmp_path, fake_backend):
    """The escape hatch must not silently hand out admin: with no token
    configured, anonymous callers on a publicly-bound server used to resolve
    to the root *admin* Caller. Now that /admin/* exists, that would be
    remote anonymous admin access."""
    settings = Settings(
        reference_dir=tmp_path,
        db_path=tmp_path / "mimic.db",
        host="0.0.0.0",
        api_token=None,
        allow_unauthenticated_public_bind=True,
    )
    client = TestClient(build_app(settings, backend_factory=lambda _s: fake_backend))
    assert client.get("/me").json()["role"] == "user"
    assert client.get("/admin/keys").status_code == 403


def test_public_bind_override_warns_admin_routes_unavailable(tmp_path, fake_backend, caplog):
    settings = Settings(
        reference_dir=tmp_path,
        db_path=tmp_path / "mimic.db",
        host="0.0.0.0",
        api_token=None,
        allow_unauthenticated_public_bind=True,
    )
    with caplog.at_level("WARNING"):
        build_app(settings, backend_factory=lambda _s: fake_backend)
    assert "admin" in caplog.text.lower()
