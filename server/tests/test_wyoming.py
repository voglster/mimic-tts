"""Unit tests for the Wyoming server's pure helpers.

We don't spin up the TCP server here — that's an integration concern. We do
verify the voice-catalogue construction, sample-to-PCM conversion, and the
synth routing logic, which is where the bugs hide.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi import HTTPException
from mimic_server.config import Settings
from mimic_server.wyoming_server import (
    _build_info,
    _route_synth,
    _samples_to_int16_bytes,
)


@pytest.fixture
def backend():
    b = MagicMock()
    b.builtin_voices.return_value = [{"name": "default", "language": "English"}]
    b.synth_builtin.return_value = (np.zeros(1024, dtype=np.float32), 22050)
    b.synth_clone.return_value = (np.zeros(1024, dtype=np.float32), 22050)
    return b


def _settings(tmp_path):
    return Settings(reference_dir=tmp_path, api_token="t")  # noqa: S106


def test_build_info_includes_builtin_voices(tmp_path, backend):
    info = _build_info(backend, _settings(tmp_path))
    assert len(info.tts) == 1
    voice_names = [v.name for v in info.tts[0].voices]
    assert "default" in voice_names


def test_build_info_includes_clone_voices_from_disk(tmp_path, backend):
    (tmp_path / "alice").mkdir()
    (tmp_path / "alice" / "audio.wav").write_bytes(b"")
    (tmp_path / "alice" / "text.txt").write_text("hi")
    info = _build_info(backend, _settings(tmp_path))
    voice_names = [v.name for v in info.tts[0].voices]
    assert "default" in voice_names
    assert "alice" in voice_names


def test_samples_to_int16_bytes_returns_pcm():
    samples = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    pcm, sr = _samples_to_int16_bytes(samples, 22050)
    assert sr == 22050
    assert len(pcm) == 2000  # 1000 samples * 2 bytes (int16)
    # Sanity: bytes decode back to int16 within range
    assert max(abs(v) for v in np.frombuffer(pcm, dtype=np.int16)) <= 32767


def test_route_synth_routes_builtin_to_synth_builtin(tmp_path, backend):
    _route_synth(backend, _settings(tmp_path), "hi", "default")
    backend.synth_builtin.assert_called_once()
    backend.synth_clone.assert_not_called()


def test_route_synth_routes_clone_to_synth_clone(tmp_path, backend):
    (tmp_path / "alice").mkdir()
    (tmp_path / "alice" / "audio.wav").write_bytes(b"")
    (tmp_path / "alice" / "text.txt").write_text("hi")
    _route_synth(backend, _settings(tmp_path), "hello", "alice")
    backend.synth_clone.assert_called_once()
    backend.synth_builtin.assert_not_called()


def test_route_synth_unknown_voice_raises_400(tmp_path, backend):
    with pytest.raises(HTTPException) as exc:
        _route_synth(backend, _settings(tmp_path), "hi", "nobody")
    assert exc.value.status_code == 400


def test_route_synth_none_voice_defaults_to_builtin(tmp_path, backend):
    _route_synth(backend, _settings(tmp_path), "hi", None)
    backend.synth_builtin.assert_called_once()
    assert backend.synth_builtin.call_args.kwargs["speaker"] == "default"
