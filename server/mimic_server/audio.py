"""Audio encoding helpers shared across route modules."""

from __future__ import annotations

import io
from typing import Any

import soundfile as sf
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse


def wav_response(samples: Any, sample_rate: int, filename: str = "output.wav") -> StreamingResponse:
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="audio/wav",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# Output containers we can hand to ffmpeg, keyed by the value clients send in
# the `format` form field. Value is (ffmpeg muxer, ffmpeg codec args, MIME).
# Bitrates are speech-tuned — libopus at 24k sounds great for voice and is
# ~4 KB/sec; libmp3lame at 64k is the universal-compatibility fallback (~8
# KB/sec) and plays on iOS < 17 too.
_FFMPEG_FORMATS: dict[str, tuple[str, list[str], str, str]] = {
    "mp3": ("mp3", ["-c:a", "libmp3lame", "-b:a", "64k"], "audio/mpeg", "mp3"),
    "opus": ("ogg", ["-c:a", "libopus", "-b:a", "24k"], "audio/ogg", "ogg"),
    "aac": ("adts", ["-c:a", "aac", "-b:a", "64k"], "audio/aac", "aac"),
}


def audio_response(
    samples: Any, sample_rate: int, fmt: str = "wav", filename_stem: str = "output"
) -> StreamingResponse | Response:
    """Encode the TTS output as the requested container. `wav` and `flac` go
    through soundfile; everything else is re-encoded by ffmpeg from a WAV
    intermediate. Defaults to wav for backwards-compatibility with the CLI
    and any Wyoming-style consumers."""
    fmt = fmt.lower()
    if fmt == "wav":
        return wav_response(samples, sample_rate, filename=f"{filename_stem}.wav")
    if fmt == "flac":
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="FLAC")
        return Response(
            content=buf.getvalue(),
            media_type="audio/flac",
            headers={"Content-Disposition": f'inline; filename="{filename_stem}.flac"'},
        )
    if fmt not in _FFMPEG_FORMATS:
        raise HTTPException(
            400,
            f"unsupported audio format {fmt!r}; supported: wav, flac, {', '.join(_FFMPEG_FORMATS)}",
        )
    # Render to WAV first (avoids juggling raw PCM dtype/shape through ffmpeg
    # stdin) and re-encode. ffmpeg autodetects WAV on stdin.
    wav_buf = io.BytesIO()
    sf.write(wav_buf, samples, sample_rate, format="WAV")
    muxer, codec_args, mime, ext = _FFMPEG_FORMATS[fmt]
    import subprocess

    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                *codec_args,
                "-f",
                muxer,
                "pipe:1",
            ],
            input=wav_buf.getvalue(),
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as e:
        raise HTTPException(500, "ffmpeg is not installed on the server") from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if e.stderr else ""
        raise HTTPException(
            500, f"audio encoding failed: {stderr.strip() or 'ffmpeg failed'}"
        ) from e
    return Response(
        content=proc.stdout,
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="{filename_stem}.{ext}"'},
    )


def transcode_to_wav(data: bytes, sample_rate: int = 24000) -> bytes:
    """Convert any audio container ffmpeg understands into mono 16-bit WAV at
    the requested rate. Used to normalize browser uploads (WebM/Opus, MP4/AAC)
    before we hand the file to a backend that only speaks soundfile-readable
    formats.

    WAV input is also accepted (idempotent re-mux), so the CLI client doesn't
    need to know which path it's on. Default rate is 24 kHz (TTS reference);
    pass 16000 for whisper/STT.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                "pipe:1",
            ],
            input=data,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as e:
        raise HTTPException(500, "ffmpeg is not installed on the server") from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if e.stderr else ""
        raise HTTPException(
            400, f"could not decode uploaded audio: {stderr.strip() or 'ffmpeg failed'}"
        ) from e
    return proc.stdout
