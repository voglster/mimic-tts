"""`mimic` CLI — typer-based command-line interface."""

from __future__ import annotations

import io
import sys
import threading
from pathlib import Path
from typing import Annotated

import typer

from mimic.client import Client
from mimic.config import load_config
from mimic.recorder import (
    DEFAULT_SAMPLE_RATE,
    pick_script,
    play,
    record_until_enter,
    save_wav,
)

app = typer.Typer(no_args_is_help=True, add_completion=False, help="mimic-tts CLI")
clone_app = typer.Typer(no_args_is_help=True, help="Clone voice operations")
app.add_typer(clone_app, name="clone")


def _client() -> Client:
    cfg = load_config()
    return Client(server_url=cfg.server_url, token=cfg.token)


BUILTIN_VOICE_NAMES: frozenset[str] = frozenset(
    {
        "Ryan",
        "Aiden",
        "Vivian",
        "Serena",
        "Uncle_Fu",
        "Dylan",
        "Eric",
        "Ono_Anna",
        "Sohee",
    }
)


@app.command()
def say(
    text: Annotated[str, typer.Argument(help="Text to synthesize.")],
    voice: Annotated[
        str | None, typer.Option(help="Built-in voice or registered clone name.")
    ] = None,
    out: Annotated[Path, typer.Option(help="Output wav path.")] = Path("out.wav"),
    language: Annotated[str, typer.Option()] = "English",
) -> None:
    """Synthesize speech. Routes to a built-in voice or a registered clone by name."""
    cfg = load_config()
    speaker = voice or cfg.default_voice
    with _client() as c:
        if speaker in BUILTIN_VOICE_NAMES:
            c.tts_to_file(text, out, speaker=speaker, language=language)
        else:
            audio = c.clone_tts(speaker, text, language=language)
            out.write_bytes(audio)
    typer.echo(f"wrote {out}")


@app.command()
def voices() -> None:
    """List built-in voices."""
    with _client() as c:
        for v in c.list_voices():
            typer.echo(f"{v['name']:12s} {v['language']}")


@app.command()
def clones() -> None:
    """List registered clone voices."""
    with _client() as c:
        for name in c.list_clones():
            typer.echo(name)


@app.command()
def health() -> None:
    """Show server health and currently loaded models."""
    with _client() as c:
        info = c.health()
    typer.echo(info)


@app.command(name="config")
def show_config() -> None:
    """Print the resolved client configuration."""
    cfg = load_config()
    typer.echo(f"server_url    {cfg.server_url}")
    typer.echo(f"token         {'<set>' if cfg.token else '<none>'}")
    typer.echo(f"default_voice {cfg.default_voice}")


@app.command()
def record(
    name: Annotated[str, typer.Argument(help="Name to register the clone under.")],
    audio: Annotated[Path | None, typer.Option(help="Skip the recorder; use this file.")] = None,
    text: Annotated[str | None, typer.Option(help="Transcript for --audio.")] = None,
) -> None:
    """Record a reference voice and register it on the server."""
    if audio is not None:
        if text is None:
            typer.echo("--text is required when --audio is provided", err=True)
            raise typer.Exit(2)
        with _client() as c:
            result = c.clone_register(name, audio, text)
        typer.echo(f"registered '{result['name']}'")
        return

    _interactive_record_and_register(name)


@clone_app.command(name="say")
def clone_say(
    name: Annotated[str, typer.Argument(help="Registered clone name.")],
    text: Annotated[str, typer.Argument()],
    out: Annotated[Path, typer.Option(help="Output wav path.")] = Path("out.wav"),
    language: Annotated[str, typer.Option()] = "English",
) -> None:
    """Synthesize speech using a registered clone voice."""
    with _client() as c:
        audio = c.clone_tts(name, text, language=language)
    out.write_bytes(audio)
    typer.echo(f"wrote {out}")


def _interactive_record_and_register(name: str) -> None:
    """Drive the guided recorder. Kept thin; primitives live in `mimic.recorder`."""
    script = pick_script()
    typer.echo(f"\nRead this script when ready:\n\n  {script}\n")
    typer.prompt("Press Enter to start recording", default="", show_default=False)

    typer.echo("Recording… press Enter to stop.")
    stop = threading.Event()

    def _wait_for_enter() -> None:
        sys.stdin.readline()
        stop.set()

    waiter = threading.Thread(target=_wait_for_enter)
    waiter.daemon = True
    waiter.start()

    result = record_until_enter(
        sample_rate=DEFAULT_SAMPLE_RATE,
        channels=1,
        max_seconds=30.0,
        stop_event=stop,
    )

    typer.echo("Playing back…")
    play(result.audio, result.sample_rate)

    keep = typer.prompt("Keep this take? [y/N/r=retry]", default="N").strip().lower()
    if keep == "r":
        _interactive_record_and_register(name)
        return
    if not keep.startswith("y"):
        typer.echo("discarded.")
        raise typer.Exit(0)

    transcript = typer.prompt("Transcript", default=script)

    buf = io.BytesIO()
    save_wav(buf, result.audio, sample_rate=result.sample_rate)
    buf.seek(0)

    with _client() as c:
        out = c.clone_register(name, buf.read(), transcript)
    typer.echo(f"registered '{out['name']}'")
