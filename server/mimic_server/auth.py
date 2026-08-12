"""Request authentication: resolve a bearer token to a Caller."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from mimic_server.errors import Forbidden, MimicError, Unauthorized
from mimic_server.identity import Caller

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

    from mimic_server.config import Settings
    from mimic_server.identity import Key, KeyStore

_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="mimic"'}


def make_caller_dependency(settings: Settings, keys: KeyStore, root: Key) -> Callable[..., Caller]:
    """Build the dependency that turns a request into a Caller.

    With no MIMIC_API_TOKEN the server is loopback-only (enforced at startup),
    so every request resolves to root and the dev workflow is unchanged.
    """
    if not settings.auth_required:

        def _local_admin() -> Caller:
            return Caller(root)

        return _local_admin

    def _authenticate(authorization: str | None = Header(default=None)) -> Caller:
        if not authorization or not authorization.startswith("Bearer "):
            raise Unauthorized("missing bearer token")
        key = keys.authenticate(authorization.removeprefix("Bearer ").strip())
        if key is None:
            raise Unauthorized("invalid, revoked, or expired token")
        keys.touch(key.id)
        return Caller(key)

    return _authenticate


def require_admin(caller: Caller) -> Caller:
    if not caller.is_admin:
        raise Forbidden("admin key required")
    return caller


def install_error_handler(app: FastAPI) -> None:
    @app.exception_handler(MimicError)
    async def _handle(_: Request, exc: MimicError) -> JSONResponse:
        headers = _CHALLENGE if exc.status == 401 else None
        return JSONResponse(status_code=exc.status, content=exc.payload(), headers=headers)

    # `validate_name` and `VoiceRegistry.set_visibility` raise plain
    # `ValueError` rather than a `MimicError` subclass — they're pure
    # validation, not domain errors tied to a specific HTTP status by
    # inheritance. Without this handler those requests would 500 instead of
    # 400.
    @app.exception_handler(ValueError)
    async def _handle_value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"error": "invalid_request", "detail": str(exc)}
        )
