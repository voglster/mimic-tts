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

STAGING_DIRNAME = ".migrate-staging"


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
    is often named after them), which makes `reference/<root.label>/` both a
    flat legacy voice dir *and* the owner directory holding already-migrated
    voices at once. Any single-pass copy-into-a-child-of-the-source approach
    ends up treating "the legacy directory" and "the owner directory" as the
    same object, and an operation meant for one clobbers the other.

    Three phases sidestep that entirely by never copying into a descendant
    of the source:

    1. Evacuate: move each legacy voice's own files (never its already-
       migrated subdirectories, if any) out to a sibling staging dir.
    2. Install: move each staged voice into its final `reference/<root>/`
       slot, a plain rename with nothing left to overlap.
    3. Reconcile: adopt anything now sitting under `reference/<root>/` that
       has no DB row yet.

    Staging is not namespaced by root label, so a leftover staged voice from
    a *previous* `MIMIC_ROOT_LABEL` gets installed under the current label
    on the next boot rather than orphaned under a name nobody points at
    anymore -- intentional: the data stays owned and visible.
    """
    staging_root = reference_dir / STAGING_DIRNAME
    _evacuate_legacy_voices(reference_dir, staging_root)
    _install_staged_voices(reference_dir, staging_root, root, registry)
    _reconcile_owner_dir(reference_dir, root, registry)


def _is_voice_dir(path: Path) -> bool:
    return path.is_dir() and (path / "audio.wav").exists()


def _evacuate_legacy_voices(reference_dir: Path, staging_root: Path) -> None:
    if not reference_dir.is_dir():
        return
    for legacy in sorted(reference_dir.iterdir()):
        if legacy.name.startswith("."):
            continue
        if not (legacy / "audio.wav").exists():
            if legacy.is_dir() and any(legacy.iterdir()):
                logger.warning(
                    "leaving %s alone: it has no audio.wav, so it is not a voice. Contents: %s",
                    legacy,
                    sorted(p.name for p in legacy.iterdir()),
                )
            continue
        if not VALID_NAME.match(legacy.name):
            logger.warning("skipping legacy voice dir with unusable name: %s", legacy.name)
            continue
        _evacuate_one(reference_dir, staging_root, legacy)


def _evacuate_one(reference_dir: Path, staging_root: Path, legacy: Path) -> None:
    """Move `legacy`'s own files to `staging_root/<name>/`, leaving any
    already-migrated voice subdirectories (the same-name/owner-dir overlap
    case) exactly where they are."""
    payload = [p for p in legacy.iterdir() if not _is_voice_dir(p)]
    if not payload:
        return  # nothing left to evacuate; only migrated voice subdirs remain

    # audio.wav moves last: while it's still present, `legacy` keeps passing
    # the `(legacy / "audio.wav").exists()` gate in `_evacuate_legacy_voices`,
    # so a crash partway through this loop always leaves a source that later
    # boots still recognize as an unfinished legacy voice and resume. Moving
    # it first would make any remaining files (transcript, notes, ...)
    # permanently unreachable, since the gate would never fire again.
    payload.sort(key=lambda p: p.name == "audio.wav")

    destination = staging_root / legacy.name
    destination.mkdir(parents=True, exist_ok=True)
    for entry in payload:
        target = destination / entry.name
        if target.exists():
            # A staged file survives here from an interrupted earlier boot.
            # `shutil.move` onto it would be the one unbacked delete left in
            # this module, and it is reachable: crash mid-evacuation, operator
            # restores the flat dir from backup, reboot. Keep both copies --
            # the staged one is already in the pipeline, so the incoming one
            # goes aside for a human to compare.
            kept = _quarantine_colliding_file(reference_dir, entry, legacy.name)
            logger.warning(
                "staged file %s already exists from an interrupted migration; "
                "kept the staged copy and moved the incoming one to %s",
                target,
                kept,
            )
            continue
        shutil.move(str(entry), str(target))

    remaining = list(legacy.iterdir()) if legacy.exists() else []
    if not remaining:
        legacy.rmdir()
    else:
        logger.warning(
            "legacy dir %s still contains %s after evacuating its flat files; "
            "verify these are already-migrated voices, not stranded data",
            legacy,
            sorted(p.name for p in remaining),
        )


def _install_staged_voices(
    reference_dir: Path, staging_root: Path, root: Key, registry: VoiceRegistry
) -> None:
    if not staging_root.is_dir():
        return
    for staged in sorted(staging_root.iterdir()):
        if (staged / "audio.wav").exists():
            _install_one(reference_dir, staged, root, registry)
        else:
            logger.warning("staged entry %s has no audio.wav; leaving it for manual review", staged)
    if not any(staging_root.iterdir()):
        staging_root.rmdir()


def _install_one(reference_dir: Path, staged: Path, root: Key, registry: VoiceRegistry) -> None:
    if not VALID_NAME.match(staged.name):
        conflict = _quarantine(reference_dir, staged, staged.name)
        logger.warning(
            "skipping staged voice dir with unusable name %r; moved it to %s",
            staged.name,
            conflict,
        )
        return

    destination = registry.dir_for(root.label, staged.name)

    if destination.exists() and not (destination / "audio.wav").exists():
        conflict = _quarantine(reference_dir, destination, staged.name)
        logger.error(
            "found an incomplete migration destination for %r; moved it to %s",
            staged.name,
            conflict,
        )

    if destination.exists():
        # Never delete a staged copy just because destination/audio.wav
        # matches -- text.txt (the reference transcript) or other payload
        # files can still differ, e.g. a corrected transcript re-recorded
        # through the flat write path. Always quarantine instead of
        # deleting; an exact duplicate only costs a little disk.
        is_duplicate = _same_payload(staged, destination)
        conflict = _quarantine(reference_dir, staged, staged.name)
        if is_duplicate:
            logger.info(
                "legacy voice %r already installed as %s/%s with identical content; "
                "moved the redundant copy to %s",
                staged.name,
                root.label,
                staged.name,
                conflict,
            )
        else:
            logger.error(
                "legacy voice %r conflicts with existing %s/%s (content differs); "
                "moved the staged copy to %s for manual review",
                staged.name,
                root.label,
                staged.name,
                conflict,
            )
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged), str(destination))
    logger.info("adopted legacy voice %r as %s/%s", staged.name, root.label, staged.name)


def _reconcile_owner_dir(reference_dir: Path, root: Key, registry: VoiceRegistry) -> None:
    """Adopt any voice already sitting under `reference/<root>/` with no DB row.

    Nothing else in the codebase scans the filesystem for voices --
    `registry.adopt` has exactly one other caller, `_install_one` above. If a
    prior boot crashed after installing a voice's files but before
    committing its row, the recording would otherwise be permanently
    invisible to the server despite being safe on disk. `adopt` is an
    upsert, so re-running this for every voice on every boot is a cheap
    no-op once it has a row.
    """
    owner_dir = reference_dir / root.label
    if not owner_dir.is_dir():
        return
    for voice_dir in sorted(owner_dir.iterdir()):
        if (voice_dir / "audio.wav").exists() and VALID_NAME.match(voice_dir.name):
            registry.adopt(root, voice_dir.name)


def _same_payload(a: Path, b: Path) -> bool:
    a_files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    b_files = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    return a_files == b_files and all(
        (a / rel).read_bytes() == (b / rel).read_bytes() for rel in a_files
    )


def _quarantine_colliding_file(reference_dir: Path, path: Path, voice_name: str) -> Path:
    """Move a single colliding file aside during evacuation.

    Grouped per voice under its own `-evacuated` directory so it cannot be
    confused with `_quarantine`'s whole-voice conflict dirs, which use bare
    and numeric-suffixed names.
    """
    holding = reference_dir / f".conflict-{voice_name}-evacuated"
    holding.mkdir(parents=True, exist_ok=True)
    target = holding / path.name
    suffix = 1
    while target.exists():
        target = holding / f"{path.name}.{suffix}"
        suffix += 1
    shutil.move(str(path), str(target))
    return target


def _quarantine(reference_dir: Path, path: Path, name: str) -> Path:
    """Move `path` aside instead of destroying it. `.`-prefixed names are
    already excluded by voice-name validation, the same property staging
    relies on, so a conflict directory can never be mistaken for a voice."""
    target = reference_dir / f".conflict-{name}"
    suffix = 1
    while target.exists():
        target = reference_dir / f".conflict-{name}-{suffix}"
        suffix += 1
    shutil.move(str(path), str(target))
    return target
