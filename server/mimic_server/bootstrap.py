"""One-time-per-boot setup: open the DB, seed the root key, adopt legacy voices."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mimic_server.db import Database
from mimic_server.identity import Key, KeyStore, generate_token, validate_label
from mimic_server.voices import VALID_NAME, VoiceRegistry

if TYPE_CHECKING:
    from pathlib import Path

    from mimic_server.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapResult:
    db: Database
    keys: KeyStore
    voices: VoiceRegistry
    root: Key


def bootstrap(settings: Settings) -> BootstrapResult:
    try:
        validate_label(settings.root_label)
    except ValueError as e:
        raise ValueError(f"MIMIC_ROOT_LABEL is invalid: {e}") from e

    db = Database(settings.db_path)
    db.migrate()
    keys = KeyStore(db)
    registry = VoiceRegistry(db, keys, settings.reference_dir)

    # In loopback dev mode there is no env token, but ownership still needs a
    # row to point at, so root gets an unguessable token nobody ever uses.
    root = keys.ensure_env_root(settings.api_token or generate_token(), settings.root_label)

    _adopt_legacy_voices(settings.reference_dir, root, registry)
    return BootstrapResult(db=db, keys=keys, voices=registry, root=root)


def _adopt_legacy_voices(reference_dir: Path, root: Key, registry: VoiceRegistry) -> None:
    """Move pre-multi-user `reference/<name>/` dirs under `reference/<root>/`.

    A legacy voice can share its name with `root.label` (someone's own voice
    is often named after them), which puts its destination *inside* its own
    source directory. `_migrate_legacy_voice` copies through a sibling
    staging directory and only ever deletes files it captured before the
    copy started, so that overlap never causes the destination to be
    clobbered by the original's removal.
    """
    if reference_dir.is_dir():
        for legacy in sorted(reference_dir.iterdir()):
            if not (legacy / "audio.wav").exists():
                continue
            if not VALID_NAME.match(legacy.name):
                logger.warning("skipping legacy voice dir with unusable name: %s", legacy.name)
                continue
            _migrate_legacy_voice(reference_dir, legacy, root, registry)

    _reconcile_owner_dir(reference_dir, root, registry)


def _reconcile_owner_dir(reference_dir: Path, root: Key, registry: VoiceRegistry) -> None:
    """Adopt any voice already sitting under `reference/<root>/` with no DB row.

    Nothing else in the codebase scans the filesystem for voices --
    `registry.adopt` has exactly one other caller, the migration above. If a
    prior boot crashed after moving a voice's files into place but before
    committing its row (or the files were placed there by some other means),
    the recording would otherwise be permanently invisible to the server
    despite being safe on disk. `adopt` is an upsert, so re-running this for
    every voice on every boot is a cheap no-op once it has a row.
    """
    owner_dir = reference_dir / root.label
    if not owner_dir.is_dir():
        return
    for voice_dir in sorted(owner_dir.iterdir()):
        if (voice_dir / "audio.wav").exists() and VALID_NAME.match(voice_dir.name):
            registry.adopt(root, voice_dir.name)


def _migrate_legacy_voice(
    reference_dir: Path, legacy: Path, root: Key, registry: VoiceRegistry
) -> None:
    destination = registry.dir_for(root.label, legacy.name)
    original_entries = [p for p in legacy.iterdir() if p != destination]

    if (destination / "audio.wav").exists():
        if not _same_audio(legacy, destination):
            conflict = _quarantine(reference_dir, legacy, legacy.name)
            logger.error(
                "legacy voice %r conflicts with existing %s/%s (different audio.wav); "
                "moved the legacy copy to %s for manual review",
                legacy.name,
                root.label,
                legacy.name,
                conflict,
            )
            return
        # Already fully migrated with matching content; fall through to clean
        # up whatever original files a prior, interrupted run left behind.
    else:
        if destination.exists():
            conflict = _quarantine(reference_dir, destination, legacy.name)
            logger.error(
                "found an incomplete migration destination for %r; moved it to %s",
                legacy.name,
                conflict,
            )
        _copy_via_staging(reference_dir, legacy, destination)

    if not (destination / "audio.wav").exists():
        logger.error("legacy voice %r failed to migrate; leaving it in place", legacy.name)
        return

    for entry in original_entries:
        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    if legacy.exists() and not any(legacy.iterdir()):
        legacy.rmdir()

    registry.adopt(root, legacy.name)
    logger.info("adopted legacy voice %r as %s/%s", legacy.name, root.label, legacy.name)


def _same_audio(legacy: Path, destination: Path) -> bool:
    return (legacy / "audio.wav").read_bytes() == (destination / "audio.wav").read_bytes()


def _quarantine(reference_dir: Path, path: Path, name: str) -> Path:
    """Move `path` aside instead of destroying it. `.`-prefixed names are
    already excluded by `VALID_NAME`, the same property the staging dir
    relies on, so a conflict directory can never be mistaken for a voice."""
    target = reference_dir / f".conflict-{name}"
    suffix = 1
    while target.exists():
        target = reference_dir / f".conflict-{name}-{suffix}"
        suffix += 1
    shutil.move(str(path), str(target))
    return target


def _copy_via_staging(reference_dir: Path, legacy: Path, destination: Path) -> None:
    """Copy `legacy` to `destination` by way of a staging dir next to it.

    A plain `copytree(legacy, destination)` breaks when `destination` is
    nested inside `legacy` (the same-name case above): staging never
    overlaps `legacy`, so the copy is always a clean, independent duplicate
    before anything gets removed. Two boots migrating the same legacy name
    concurrently would clobber each other's staging directory -- harmless
    (no data loss, since `legacy` itself is untouched until the move
    succeeds) but worth knowing if migration ever runs from more than one
    process at a time.
    """
    staging = reference_dir / f".bootstrap-migrate-{legacy.name}"
    if staging.exists():
        shutil.rmtree(staging)  # discard a stale partial copy from a prior crash
    try:
        shutil.copytree(legacy, staging)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(destination))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
