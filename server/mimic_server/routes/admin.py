"""Admin surface: mint/list/patch/revoke keys, server-wide usage and voices."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from mimic_server.auth import require_admin
from mimic_server.errors import Forbidden, InvalidRequest, KeyNotFound
from mimic_server.identity import DEFAULT_DAILY_CHAR_QUOTA, DEFAULT_MAX_VOICES

if TYPE_CHECKING:
    from fastapi import FastAPI

    from mimic_server.identity import Caller, Key
    from mimic_server.services import Services

_ROOT_KEY_IMMUTABLE = "the root key is managed by MIMIC_API_TOKEN and cannot be modified"
_VALID_ROLES = frozenset({"user", "admin"})

# Only these survive on a managed_by_env (root) key: it is the unrevokable
# recovery path if a minted admin key is lost, so the default for any field
# not named here is "reject", not "allow" -- expires_at, enabled, and role
# are all safety-relevant and must never reach it through this route.
_ROOT_PATCHABLE_FIELDS = frozenset({"notes", "max_voices", "daily_char_quota", "can_upload"})

# expires_at is the one field where an explicit JSON null is the legitimate
# "clear the expiry" request. Every other field maps to a NOT NULL column;
# an explicit null there is bad input, not a valid update.
_NULLABLE_PATCH_FIELDS = frozenset({"expires_at"})

# 0 means "unlimited" (see usage.UsageTracker.check_quota's `limit <= 0`
# check), not "no allowance" -- disable a key with enabled=False instead.


class _MintBody(BaseModel):
    # Pydantic's default extra="ignore" would silently drop a misspelled or
    # unknown field and still 200 -- worst exactly where it matters, since a
    # dropped field means the request did nothing but looks like it worked.
    # "forbid" turns that into a 422, hardening the allowlists below by
    # construction: an unknown key can no longer reach model_dump() at all.
    model_config = ConfigDict(extra="forbid")

    label: str
    role: str = "user"
    can_upload: bool = True
    max_voices: int = Field(default=DEFAULT_MAX_VOICES, ge=0)
    daily_char_quota: int = Field(default=DEFAULT_DAILY_CHAR_QUOTA, ge=0)
    expires_at: str | None = None
    notes: str = ""


class _PatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    can_upload: bool | None = None
    max_voices: int | None = Field(default=None, ge=0)
    daily_char_quota: int | None = Field(default=None, ge=0)
    expires_at: str | None = None
    role: str | None = None
    notes: str | None = None


def _key_json(key: Key, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "label": key.label,
        "token_prefix": key.token_prefix,
        "role": key.role,
        "enabled": key.enabled,
        "created_at": key.created_at,
        "last_used_at": key.last_used_at,
        "expires_at": key.expires_at,
        "can_upload": key.can_upload,
        "max_voices": key.max_voices,
        "daily_char_quota": key.daily_char_quota,
        "managed_by_env": key.managed_by_env,
        "notes": key.notes,
        "usage": usage or {"requests": 0, "chars": 0, "audio_seconds": 0.0},
    }


def _get_key_or_404(svc: Services, label: str) -> Key:
    key = svc.keys.get_by_label(label)
    if key is None:
        raise KeyNotFound(f"no key labeled {label!r}")
    return key


def _guard_root_mutation(key: Key, fields: dict[str, Any]) -> None:
    """The root key is the recovery path when a minted admin key is lost.

    An earlier version of this guard denied specific dangerous fields
    (enabled, role) and allowed everything else by default -- which meant
    PATCH /admin/keys/root {"expires_at": "<past>"} slipped through and
    silently expired the one key with no in-band recovery. An allowlist
    closes that off for every field, present and future: anything not named
    in _ROOT_PATCHABLE_FIELDS is refused, full stop.
    """
    if not key.managed_by_env:
        return
    disallowed = set(fields) - _ROOT_PATCHABLE_FIELDS
    if disallowed:
        raise Forbidden(_ROOT_KEY_IMMUTABLE)


def _validate_role(role: str | None) -> None:
    if role is not None and role not in _VALID_ROLES:
        raise InvalidRequest(f"role must be one of {sorted(_VALID_ROLES)}, got {role!r}")


def _reject_illegal_nulls(fields: dict[str, Any]) -> None:
    illegal = sorted(
        name
        for name, value in fields.items()
        if value is None and name not in _NULLABLE_PATCH_FIELDS
    )
    if illegal:
        raise InvalidRequest(f"{illegal} cannot be null")


def register(app: FastAPI, svc: Services) -> None:
    # `caller` takes svc.caller as a real default, not via `Annotated[Caller,
    # svc.caller]` — `from __future__ import annotations` stringifies
    # annotations, and FastAPI resolves those strings against this module's
    # globals only, never this closure's `svc`. Every handler below calls
    # `require_admin(caller)` as its first statement. Split into two
    # registrars purely to keep mccabe complexity per function down — they
    # share no state beyond `app` and `svc`.
    _register_key_routes(app, svc)
    _register_read_routes(app, svc)


def _register_key_routes(app: FastAPI, svc: Services) -> None:
    @app.get("/admin/keys")
    async def admin_list_keys(caller: Caller = svc.caller) -> dict[str, Any]:
        require_admin(caller)
        usage_by_label = {t["label"]: t for t in svc.usage.totals()}
        return {
            "keys": [_key_json(key, usage_by_label.get(key.label)) for key in svc.keys.list_all()]
        }

    @app.post("/admin/keys")
    async def admin_mint_key(body: _MintBody, caller: Caller = svc.caller) -> dict[str, Any]:
        require_admin(caller)
        _validate_role(body.role)
        key, token = svc.keys.create(
            body.label,
            role=body.role,
            can_upload=body.can_upload,
            max_voices=body.max_voices,
            daily_char_quota=body.daily_char_quota,
            expires_at=body.expires_at,
            notes=body.notes,
        )
        return {**_key_json(key), "token": token}

    @app.patch("/admin/keys/{label}")
    async def admin_patch_key(
        label: str, body: _PatchBody, caller: Caller = svc.caller
    ) -> dict[str, Any]:
        require_admin(caller)
        key = _get_key_or_404(svc, label)
        fields = body.model_dump(exclude_unset=True)
        _reject_illegal_nulls(fields)
        _validate_role(fields.get("role"))
        _guard_root_mutation(key, fields)
        updated = svc.keys.update(label, **fields) if fields else key
        return _key_json(updated)

    @app.delete("/admin/keys/{label}")
    async def admin_delete_key(
        label: str, purge: bool = False, caller: Caller = svc.caller
    ) -> dict[str, Any]:
        require_admin(caller)
        key = _get_key_or_404(svc, label)
        if key.managed_by_env:
            raise Forbidden(_ROOT_KEY_IMMUTABLE)
        if purge:
            for voice in svc.voices.all_voices():
                if voice.owner_label == label:
                    shutil.rmtree(svc.voices.dir_for(label, voice.name), ignore_errors=True)
            svc.keys.delete(label)
        else:
            svc.keys.update(label, enabled=False)
        return {"status": "ok", "label": label, "purged": purge}


def _register_read_routes(app: FastAPI, svc: Services) -> None:
    @app.get("/admin/usage")
    async def admin_usage(
        key: str | None = None,
        since: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        caller: Caller = svc.caller,
    ) -> dict[str, Any]:
        require_admin(caller)
        key_id = _get_key_or_404(svc, key).id if key is not None else None
        return {
            "totals": svc.usage.totals(key_id=key_id, since=since),
            "events": svc.usage.events(key_id=key_id, since=since, limit=limit),
        }

    @app.get("/admin/voices")
    async def admin_voices(caller: Caller = svc.caller) -> dict[str, Any]:
        require_admin(caller)
        return {
            "voices": [
                {
                    "qualified": voice.qualified,
                    "name": voice.name,
                    "owner": voice.owner_label,
                    "visibility": voice.visibility,
                    "created_at": voice.created_at,
                    "grants": svc.voices.grants_for(voice),
                }
                for voice in svc.voices.all_voices()
            ]
        }
