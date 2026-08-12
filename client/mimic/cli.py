"""`mimic` CLI — typer-based command-line interface."""

from __future__ import annotations

import io
import sys
import threading
from pathlib import Path  # noqa: TC003 — typer evaluates annotations at runtime
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from collections.abc import Callable

from mimic.admin_cli import admin_app
from mimic.client import Client
from mimic.config import load_config
from mimic.errors import (
    MimicAPIError,
    MimicAuthError,
    MimicForbiddenError,
    MimicNotFoundError,
    MimicQuotaError,
    MimicValidationError,
)
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
app.add_typer(admin_app, name="admin")


def _format_validation_error(e: MimicValidationError) -> str:
    """Turn a validation failure's body into one readable line.

    Covers FastAPI's list-shaped 422 `detail` (field + msg per item) and the
    409 ambiguous-voice-name case, which carries a `candidates` list alongside
    a plain `detail` string. Anything else falls back to the raw message.
    """
    detail = e.body.get("detail")
    if isinstance(detail, list):
        fields = []
        for item in detail:
            loc = [str(p) for p in item.get("loc", []) if p != "body"]
            field = ".".join(loc) or "request"
            fields.append(f"{field}: {item.get('msg', '')}")
        return "; ".join(fields)
    candidates = e.body.get("candidates")
    if candidates:
        return f"{e.message}: {', '.join(candidates)}"
    return e.message


def _run[T](action: Callable[[], T]) -> T:
    """Turn API errors into a one-line message and a non-zero exit.

    A traceback is the wrong output for 'your friend's key ran out of quota'.
    """
    try:
        return action()
    except MimicQuotaError as e:
        message = f"quota exceeded: {e.used:,} / {e.limit:,} characters today"
        if e.resets_at:
            message += f" (resets at {e.resets_at})"
        typer.echo(message, err=True)
        raise typer.Exit(1) from e
    except MimicForbiddenError as e:
        typer.echo(f"not permitted: {e.message}", err=True)
        raise typer.Exit(1) from e
    except MimicAuthError as e:
        typer.echo(f"authentication failed: {e.message}", err=True)
        typer.echo("check `token` in ~/.config/mimic/config.toml", err=True)
        raise typer.Exit(1) from e
    except MimicNotFoundError as e:
        typer.echo(f"not found: {e.message}", err=True)
        raise typer.Exit(1) from e
    except MimicValidationError as e:
        typer.echo(f"invalid request: {_format_validation_error(e)}", err=True)
        raise typer.Exit(1) from e
    except MimicAPIError as e:
        typer.echo(f"error: {e.message}", err=True)
        raise typer.Exit(1) from e


def _client() -> Client:
    cfg = load_config()
    return Client(server_url=cfg.server_url, token=cfg.token)


BUILTIN_VOICE_NAMES: frozenset[str] = frozenset({"default"})


def _play_wav_bytes(wav: bytes) -> None:
    """Decode a WAV byte string and play it through the default output device."""
    import io as _io

    import soundfile as _sf

    audio, sr = _sf.read(_io.BytesIO(wav), dtype="float32", always_2d=True)
    play(audio, sr)


@app.command()
def say(
    text: Annotated[str, typer.Argument(help="Text to synthesize.")],
    voice: Annotated[
        str | None, typer.Option(help="Built-in voice or registered clone name.")
    ] = None,
    out: Annotated[
        Path | None, typer.Option(help="Write WAV to this path instead of playing.")
    ] = None,
    language: Annotated[str, typer.Option()] = "English",
) -> None:
    """Synthesize speech and play it. Pass --out FILE to save instead of play."""
    cfg = load_config()
    speaker = voice or cfg.default_voice
    with _client() as c:
        if speaker in BUILTIN_VOICE_NAMES:
            audio = _run(lambda: c.tts(text, speaker=speaker, language=language))
        else:
            audio = _run(lambda: c.clone_tts(speaker, text, language=language))
    if out is None:
        _play_wav_bytes(audio)
    else:
        out.write_bytes(audio)
        typer.echo(f"wrote {out}")


@app.command()
def voices() -> None:
    """List built-in voices."""
    with _client() as c:
        for v in _run(c.list_voices):
            typer.echo(f"{v['name']:12s} {v['language']}")


@app.command()
def clones(mine: Annotated[bool, typer.Option(help="Only voices you own.")] = False) -> None:
    """List clone voices you can use."""
    with _client() as c:
        detail = _run(c.list_clone_detail)
        if not detail:
            for name in _run(c.list_clones):
                typer.echo(name)
            return
        for v in detail:
            if mine and not v["mine"]:
                continue
            marker = "*" if v["mine"] else " "
            typer.echo(f"{marker} {v['qualified']:32s} {v['visibility']}")


@app.command()
def health() -> None:
    """Show server status and backend. (Loaded models moved to `mimic whoami`.)"""
    with _client() as c:
        info = _run(c.health)
    typer.echo(info)


@app.command()
def whoami() -> None:
    """Show which key you are, what you may do, and today's usage."""
    with _client() as c:
        me = _run(c.whoami)
    quota = me["daily_char_quota"]
    used = me["usage_today"]["chars"]
    budget = "unlimited" if quota == 0 else f"{used:,} / {quota:,}"
    typer.echo(f"key           {me['label']} ({me['role']})")
    typer.echo(f"upload        {'yes' if me['can_upload'] else 'no'}")
    typer.echo(f"voices        {me['voices_used']} / {me['max_voices']}")
    typer.echo(f"chars today   {budget}")
    typer.echo(f"requests      {me['usage_today']['requests']}")


@app.command()
def share(
    voice: Annotated[str, typer.Argument(help="Voice name, or owner/name.")],
    to: Annotated[str | None, typer.Option("--to", help="Grant to this key label.")] = None,
    public: Annotated[bool, typer.Option("--public", help="Let every key use it.")] = False,
    private: Annotated[bool, typer.Option("--private", help="Unpublish it.")] = False,
) -> None:
    """Share a voice with one person, or publish it to everyone."""
    chosen = [bool(to), public, private]
    if sum(chosen) != 1:
        typer.echo("pass exactly one of --to LABEL, --public, or --private", err=True)
        raise typer.Exit(2)
    with _client() as c:
        if to:
            _run(lambda: c.grant_voice(voice, to))
            typer.echo(f"shared {voice} with {to}")
        else:
            visibility = "public" if public else "private"
            _run(lambda: c.set_visibility(voice, visibility))
            typer.echo(f"{voice} is now {visibility}")


@app.command()
def unshare(
    voice: Annotated[str, typer.Argument(help="Voice name, or owner/name.")],
    from_: Annotated[str, typer.Option("--from", help="Key label to revoke.")],
) -> None:
    """Revoke one person's access to a voice."""
    with _client() as c:
        _run(lambda: c.revoke_voice_grant(voice, from_))
    typer.echo(f"revoked {from_}'s access to {voice}")


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
            result = _run(lambda: c.clone_register(name, audio, text))
        typer.echo(f"registered '{result['name']}'")
        return

    _interactive_record_and_register(name)


@clone_app.command(name="say")
def clone_say(
    name: Annotated[str, typer.Argument(help="Registered clone name.")],
    text: Annotated[str, typer.Argument()],
    out: Annotated[
        Path | None, typer.Option(help="Write WAV to this path instead of playing.")
    ] = None,
    language: Annotated[str, typer.Option()] = "English",
) -> None:
    """Synthesize speech using a registered clone voice and play it."""
    with _client() as c:
        audio = _run(lambda: c.clone_tts(name, text, language=language))
    if out is None:
        _play_wav_bytes(audio)
    else:
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
        out = _run(lambda: c.clone_register(name, buf.read(), transcript))
    typer.echo(f"registered '{out['name']}'")
