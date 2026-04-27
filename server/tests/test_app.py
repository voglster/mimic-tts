from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from mimic_server.app import build_app
from mimic_server.config import Settings


@pytest.fixture
def fake_model():
    m = MagicMock()
    m.generate_custom_voice.return_value = ([b"\x00\x01"], 24000)
    m.generate_voice_clone.return_value = ([b"\x00\x01"], 24000)
    m.create_voice_clone_prompt.return_value = object()
    return m


def test_health_no_auth(tmp_path, fake_model):
    settings = Settings(reference_dir=tmp_path, api_token=None)
    app = build_app(settings, model_loader=lambda mid: fake_model)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_voices_unauthenticated_when_no_token(tmp_path, fake_model):
    settings = Settings(reference_dir=tmp_path, api_token=None)
    app = build_app(settings, model_loader=lambda mid: fake_model)
    client = TestClient(app)
    assert client.get("/voices").status_code == 200


def test_protected_route_rejects_without_token(tmp_path, fake_model):
    settings = Settings(reference_dir=tmp_path, api_token="shhh")
    app = build_app(settings, model_loader=lambda mid: fake_model)
    client = TestClient(app)
    assert client.get("/voices").status_code == 401


def test_health_remains_open_even_with_token(tmp_path, fake_model):
    settings = Settings(reference_dir=tmp_path, api_token="shhh")
    app = build_app(settings, model_loader=lambda mid: fake_model)
    client = TestClient(app)
    assert client.get("/health").status_code == 200


def test_tts_endpoint_returns_wav(tmp_path, fake_model):
    import numpy as np
    fake_model.generate_custom_voice.return_value = ([np.zeros(1024, dtype=np.float32)], 24000)

    settings = Settings(reference_dir=tmp_path, api_token=None)
    app = build_app(settings, model_loader=lambda mid: fake_model)
    client = TestClient(app)

    r = client.post("/tts", data={"text": "hello", "speaker": "Ryan"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
