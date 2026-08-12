"""Shared fixtures for route-level tests (clones today, tts/oneshot in Task 10)."""

from __future__ import annotations

import io
import itertools
from unittest.mock import MagicMock

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from mimic_server.app import build_app
from mimic_server.bootstrap import bootstrap
from mimic_server.config import Settings
from mimic_server.services import Services, assemble_services


def _wav() -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(12000, dtype=np.float32), 24000, format="WAV", subtype="PCM_16")
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


def _services(tmp_path, fake_backend, **kw: object) -> Services:
    """Assemble a `Services` bundle the same way `build_app` does, for tests
    that want direct access to it instead of going through the HTTP layer."""
    settings = Settings(
        reference_dir=tmp_path / "reference",
        db_path=tmp_path / "mimic.db",
        **kw,
    )
    settings.reference_dir.mkdir(parents=True, exist_ok=True)
    return assemble_services(settings, fake_backend)


@pytest.fixture
def env(tmp_path, fake_backend):
    settings = Settings(
        reference_dir=tmp_path / "reference",
        db_path=tmp_path / "mimic.db",
        api_token="root-token",  # noqa: S106
    )
    app = build_app(settings, backend_factory=lambda _s: fake_backend)
    client = TestClient(app)
    keys = bootstrap(settings).keys
    _, dave = keys.create("dave")
    _, erin = keys.create("erin")
    return client, {"root": "root-token", "dave": dave, "erin": erin}, keys


def _auth(tokens, who):
    return {"Authorization": f"Bearer {tokens[who]}"}


def _register(client, tokens, who, name):
    return client.post(
        "/clone/register",
        headers=_auth(tokens, who),
        data={"name": name, "ref_text": "hello"},
        files={"ref_audio": ("a.wav", _wav(), "audio/wav")},
    )


@pytest.fixture
def matrix_env(tmp_path_factory, fake_backend):
    """Factory fixture for the authorization matrix: each call builds a
    brand-new app, DB, and reference dir, so a destructive verb exercised by
    one (case, actor) combination can never leak state into another. `dave`
    owns a private voice `warm`; `erin` has no relationship to it.
    """
    build_count = itertools.count()

    def _build() -> tuple[TestClient, dict[str, str]]:
        root = tmp_path_factory.mktemp(f"matrix-{next(build_count)}")
        settings = Settings(
            reference_dir=root / "reference",
            db_path=root / "mimic.db",
            api_token="root-token",  # noqa: S106
        )
        app = build_app(settings, backend_factory=lambda _s: fake_backend)
        client = TestClient(app)
        keys = bootstrap(settings).keys
        _, dave = keys.create("dave")
        _, erin = keys.create("erin")
        tokens = {"root": "root-token", "dave": dave, "erin": erin}
        _register(client, tokens, "dave", "warm")
        return client, tokens

    return _build
