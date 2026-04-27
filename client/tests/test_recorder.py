import io
from unittest.mock import MagicMock

import numpy as np
from mimic.recorder import (
    PROMPT_SCRIPTS,
    RecordingResult,
    pick_script,
    record_until_enter,
    save_wav,
)


def test_pick_script_returns_one_of_the_known_scripts():
    s = pick_script(rng=__import__("random").Random(0))
    assert s in PROMPT_SCRIPTS
    assert len(s.split()) >= 5


def test_pick_script_is_deterministic_with_seeded_rng():
    import random

    a = pick_script(rng=random.Random(42))  # noqa: S311
    b = pick_script(rng=random.Random(42))  # noqa: S311
    assert a == b


def test_record_until_enter_collects_audio_until_signal_set():
    fake_chunks = [
        np.array([[0.1], [0.2]], dtype=np.float32),
        np.array([[0.3], [0.4]], dtype=np.float32),
    ]

    class FakeStream:
        def __init__(self, *_args, **kwargs):
            self.callback = kwargs["callback"]

        def __enter__(self):
            for chunk in fake_chunks:
                self.callback(chunk, len(chunk), None, None)
            return self

        def __exit__(self, *exc):
            return False

    stop = MagicMock()
    stop.wait.return_value = None

    result = record_until_enter(
        sample_rate=24000,
        channels=1,
        max_seconds=30,
        stop_event=stop,
        stream_factory=FakeStream,
    )

    assert isinstance(result, RecordingResult)
    assert result.sample_rate == 24000
    assert result.channels == 1
    assert result.audio.shape == (4, 1)
    np.testing.assert_allclose(
        result.audio.flatten(),
        [0.1, 0.2, 0.3, 0.4],
        rtol=1e-5,
    )


def test_save_wav_writes_a_readable_wav(tmp_path):
    audio = np.zeros((24000, 1), dtype=np.float32)
    out = tmp_path / "out.wav"
    save_wav(out, audio, sample_rate=24000)
    import soundfile as sf

    data, sr = sf.read(out)
    assert sr == 24000
    assert len(data) == 24000


def test_save_wav_to_buffer():
    audio = np.zeros((1000, 1), dtype=np.float32)
    buf = io.BytesIO()
    save_wav(buf, audio, sample_rate=24000)
    buf.seek(0)
    import soundfile as sf

    _, sr = sf.read(buf)
    assert sr == 24000
