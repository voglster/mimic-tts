"""`mimic admin` — key minting, revocation, usage, and server-wide voice listing."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer

admin_app = typer.Typer(no_args_is_help=True, help="Admin operations (requires an admin key)")
key_app = typer.Typer(no_args_is_help=True, help="API key management")
admin_app.add_typer(key_app, name="key")

_DURATION_RE = re.compile(r"^(\d+)([dh])$")


def _parse_expiry(value: str) -> str:
    """Accept a bare date (`2027-01-01`) or a duration (`90d`, `12h`); return ISO-8601 UTC.

    The server independently normalizes and validates `expires_at` — this is
    convenience parsing for the terminal, not validation of record.
    """
    duration = _DURATION_RE.match(value)
    if duration:
        amount, unit = int(duration.group(1)), duration.group(2)
        delta = timedelta(days=amount) if unit == "d" else timedelta(hours=amount)
        return (datetime.now(UTC) + delta).isoformat()
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC).isoformat()
    except ValueError as e:
        raise typer.BadParameter(
            f"{value!r} is not a date (YYYY-MM-DD) or a duration (e.g. 90d, 12h)"
        ) from e


def _chars_today_label(used: int, quota: int) -> str:
    return "unlimited" if quota == 0 else f"{used:,} / {quota:,}"


def _short_timestamp(value: str | None) -> str:
    """Trim an ISO-8601 timestamp to `YYYY-MM-DDTHH:MM:SS` for table display."""
    return "never" if value is None else value[:19]


def _row(*cells: tuple[str, int]) -> str:
    """Join `(text, width)` cells with a guaranteed gap, so a value wider than
    its column (a long timestamp, a long grant list) never runs into the next
    column instead of just looking a little ragged."""
    return "  ".join(text.ljust(width) for text, width in cells).rstrip()


@key_app.command("create")
def key_create(
    label: Annotated[str, typer.Argument(help="Short name for this key, e.g. 'dave'.")],
    quota: Annotated[int | None, typer.Option(help="Daily character limit; 0 = unlimited.")] = None,
    max_voices: Annotated[
        int | None, typer.Option(help="How many voices they may upload; 0 = unlimited.")
    ] = None,
    no_upload: Annotated[bool, typer.Option("--no-upload", help="Forbid uploading voices.")] = (
        False
    ),
    expires: Annotated[
        str | None, typer.Option(help="Expiry as a date (2027-01-01) or duration (90d, 12h).")
    ] = None,
    admin: Annotated[bool, typer.Option("--admin", help="Mint an admin key.")] = False,
    notes: Annotated[str | None, typer.Option(help="Free-text note.")] = None,
) -> None:
    """Mint a new API key. The token is printed once and cannot be recovered."""
    from mimic.cli import _client, _run

    fields: dict[str, object] = {}
    if quota is not None:
        fields["daily_char_quota"] = quota
    if max_voices is not None:
        fields["max_voices"] = max_voices
    if no_upload:
        fields["can_upload"] = False
    if expires is not None:
        fields["expires_at"] = _parse_expiry(expires)
    if admin:
        fields["role"] = "admin"
    if notes is not None:
        fields["notes"] = notes

    with _client() as c:
        created = _run(lambda: c.create_key(label, **fields))

    typer.echo(f"key '{created['label']}' created\n")
    typer.echo(f"  {created['token']}\n")
    typer.secho(
        "This token is shown once. Copy it now — the server stores only a hash.",
        fg=typer.colors.YELLOW,
    )


@key_app.command("revoke")
def key_revoke(
    label: Annotated[str, typer.Argument(help="Key label to revoke.")],
    purge: Annotated[
        bool,
        typer.Option("--purge", help="Also delete their uploaded voices. Irreversible."),
    ] = False,
) -> None:
    """Revoke a key. By default this is soft (the key stops working, nothing is deleted)."""
    from mimic.cli import _client, _run

    if purge:
        typer.confirm(f"Permanently delete {label} and every voice they uploaded?", abort=True)
    with _client() as c:
        _run(lambda: c.revoke_key(label, purge=purge))
    typer.echo(f"revoked '{label}'" + (" and purged their voices" if purge else ""))


@admin_app.command("keys")
def keys() -> None:
    """List every API key: role, state, last use, and today's usage."""
    from mimic.cli import _client, _run

    with _client() as c:
        rows = _run(c.list_keys)

    typer.echo(
        _row(
            ("LABEL", 16),
            ("PREFIX", 12),
            ("ROLE", 8),
            ("STATE", 10),
            ("LAST USED", 19),
            ("CHARS TODAY", 0),
        )
    )
    has_root = False
    for row in rows:
        is_root = row.get("managed_by_env", False)
        has_root = has_root or is_root
        label = f"{row['label']}*" if is_root else row["label"]
        state = "active" if row["enabled"] else "revoked"
        last_used = _short_timestamp(row["last_used_at"])
        chars_today = _chars_today_label(row["usage"]["chars"], row["daily_char_quota"])
        typer.echo(
            _row(
                (label, 16),
                (row["token_prefix"], 12),
                (row["role"], 8),
                (state, 10),
                (last_used, 19),
                (chars_today, 0),
            )
        )
    if has_root:
        typer.echo("\n* env-managed root key — cannot be revoked, purged, or demoted")


@admin_app.command("usage")
def usage(
    key: Annotated[str | None, typer.Option(help="Only this key's usage.")] = None,
    since: Annotated[
        str | None, typer.Option(help="Only events after this date or duration ago.")
    ] = None,
    events: Annotated[bool, typer.Option("--events", help="Also print the raw request log.")] = (
        False
    ),
) -> None:
    """Show per-key usage totals across the server."""
    from mimic.cli import _client, _run

    resolved_since = _parse_expiry(since) if since else None
    with _client() as c:
        data = _run(lambda: c.admin_usage(key=key, since=resolved_since))

    typer.echo(_row(("LABEL", 16), ("REQUESTS", 12), ("CHARS", 12), ("AUDIO SECONDS", 0)))
    for row in data["totals"]:
        chars = f"{row['chars']:,}"
        typer.echo(
            _row(
                (row["label"], 16),
                (str(row["requests"]), 12),
                (chars, 12),
                (str(row["audio_seconds"]), 0),
            )
        )

    if events:
        typer.echo("\nEVENTS")
        for event in data["events"]:
            typer.echo(event)


@admin_app.command("voices")
def voices() -> None:
    """List every voice on the server: owner, visibility, and who it's shared with."""
    from mimic.cli import _client, _run

    with _client() as c:
        rows = _run(c.admin_voices)

    typer.echo(_row(("QUALIFIED", 28), ("VISIBILITY", 12), ("SHARED WITH", 0)))
    for row in rows:
        shared_with = ", ".join(row["grants"]) if row["grants"] else "—"
        typer.echo(_row((row["qualified"], 28), (row["visibility"], 12), (shared_with, 0)))
