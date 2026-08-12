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
    if not reference_dir.is_dir():
        return
    for legacy in sorted(reference_dir.iterdir()):
        if not (legacy / "audio.wav").exists():
            continue
        if not VALID_NAME.match(legacy.name):
            logger.warning("skipping legacy voice dir with unusable name: %s", legacy.name)
            continue
        _migrate_legacy_voice(reference_dir, legacy, root, registry)


def _migrate_legacy_voice(
    reference_dir: Path, legacy: Path, root: Key, registry: VoiceRegistry
) -> None:
    destination = registry.dir_for(root.label, legacy.name)
    original_entries = [p for p in legacy.iterdir() if p != destination]

    if not (destination / "audio.wav").exists():
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


def _copy_via_staging(reference_dir: Path, legacy: Path, destination: Path) -> None:
    """Copy `legacy` to `destination` by way of a staging dir next to it.

    A plain `copytree(legacy, destination)` breaks when `destination` is
    nested inside `legacy` (the same-name case above): staging never
    overlaps `legacy`, so the copy is always a clean, independent duplicate
    before anything gets removed.
    """
    if destination.exists() and not (destination / "audio.wav").exists():
        shutil.rmtree(destination)  # discard a corrupt copy from a prior crash
    if destination.exists():
        return
    staging = reference_dir / f".bootstrap-migrate-{legacy.name}"
    if staging.exists():
        shutil.rmtree(staging)  # discard a stale partial copy from a prior crash
    shutil.copytree(legacy, staging)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging), str(destination))
