import numpy as np
import pytest
from fastapi import HTTPException
from mimic_server.backends import chatterbox as cb
from mimic_server.config import Settings


class FakeChatterbox:
    sr = 24000

    def __init__(self) -> None:
        self.generate_calls: list[dict] = []
        self.generate_error: Exception | None = None

    def generate(self, **kwargs) -> np.ndarray:
        self.generate_calls.append(kwargs)
        if self.generate_error is not None:
            raise self.generate_error
        return np.zeros(64, dtype=np.float32)


@pytest.fixture
def backend(tmp_path, monkeypatch):
    releases: list[int] = []
    model = FakeChatterbox()
    monkeypatch.setattr(cb, "_empty_cuda_cache", lambda: releases.append(len(model.generate_calls)))
    b = cb.ChatterboxBackend(
        Settings(reference_dir=tmp_path, unload_after=0),
        loader=lambda _model_id: model,
    )
    return b, model, releases


def test_builtin_synth_releases_cuda_cache(backend):
    b, _model, releases = backend
    b.synth_builtin(text="hello", speaker="default")
    assert releases == [1]


def test_clone_synth_releases_cuda_cache(backend, tmp_path):
    b, _model, releases = backend
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"")
    b.synth_clone(name="alice", text="hello", ref_audio_path=ref, ref_text="")
    assert releases == [1]


def test_oneshot_synth_releases_cuda_cache(backend):
    b, _model, releases = backend
    b.synth_clone_oneshot(text="hello", ref_audio_bytes=b"", ref_text="")
    assert releases == [1]


def test_cuda_cache_released_when_generate_raises(backend):
    b, model, releases = backend
    model.generate_error = RuntimeError("CUDA out of memory")

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        b.synth_builtin(text="hello", speaker="default")

    assert releases == [1]


def test_rejected_speaker_does_not_reach_the_model(backend):
    b, model, releases = backend

    with pytest.raises(HTTPException, match="no built-in voice"):
        b.synth_builtin(text="hello", speaker="nobody")

    assert model.generate_calls == []
    assert releases == []
