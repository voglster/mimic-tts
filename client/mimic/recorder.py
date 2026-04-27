"""Microphone recording flow for the `mimic record` CLI command."""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import numpy as np
import sounddevice as sd
import soundfile as sf

PROMPT_SCRIPTS: tuple[str, ...] = (
    "The quick brown fox jumps over the lazy dog while a thunderstorm rolls in.",
    "Could you please bring me a glass of water and a small slice of bread?",
    "Each spring the cherry trees blossom and turn the entire park into a sea of pink.",
    "I'd like to visit the museum tomorrow afternoon if the weather stays clear.",
    "Numbers like seventy-three and one hundred and forty-two are surprisingly tricky to say.",
    "She whispered carefully so that no one in the dim hallway would hear them speak.",
)

DEFAULT_SAMPLE_RATE = 24000
DEFAULT_CHANNELS = 1


@dataclass
class RecordingResult:
    audio: np.ndarray
    sample_rate: int
    channels: int


def pick_script(rng: random.Random | None = None) -> str:
    """Pick a random prompt script. Pass a seeded `Random` for determinism."""
    return (rng or random).choice(PROMPT_SCRIPTS)


def record_until_enter(
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    max_seconds: float = 30.0,
    stop_event: threading.Event | None = None,
) -> RecordingResult:
    """Capture audio from the default mic until `stop_event` fires or max_seconds elapses."""
    stop = stop_event or threading.Event()
    chunks: list[np.ndarray] = []

    def callback(indata: np.ndarray, frames: int, time_info, status) -> None:
        chunks.append(indata.copy())

    with sd.InputStream(
        samplerate=sample_rate, channels=channels, callback=callback,
    ):
        stop.wait(timeout=max_seconds)

    audio = np.concatenate(chunks, axis=0) if chunks else np.zeros((0, channels), np.float32)
    return RecordingResult(audio=audio, sample_rate=sample_rate, channels=channels)


def save_wav(out: Path | str | IO[bytes], audio: np.ndarray, *, sample_rate: int) -> None:
    """Write a (frames, channels) float32 array as a WAV."""
    sf.write(out, audio, sample_rate, format="WAV", subtype="PCM_16")


def play(audio: np.ndarray, sample_rate: int) -> None:
    """Block until playback finishes. Used by the CLI for review."""
    sd.play(audio, samplerate=sample_rate)
    sd.wait()
