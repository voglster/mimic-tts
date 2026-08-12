"""Request authentication: resolve a bearer token to a Caller."""

from __future__ import annotations

import logging
from dataclasses import replace
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

logger = logging.getLogger(__name__)


def make_caller_dependency(settings: Settings, keys: KeyStore, root: Key) -> Callable[..., Caller]:
    """Build the dependency that turns a request into a Caller.

    With no MIMIC_API_TOKEN the server is loopback-only (enforced at startup)
    *unless* MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND=1 overrides that check for
    a deployment where a reverse proxy or tailnet ACL enforces access
    upstream. Loopback dev mode resolves every request to root, unchanged.
    The public-bind escape hatch must not do the same — that would hand
    remote, unauthenticated callers admin access to /admin/*. It resolves
    anonymous callers to a non-admin identity instead, sharing root's
    underlying key row (so voice ownership and usage tracking still have a
    real foreign key to point at) but with its role demoted.
    """
    if not settings.auth_required:
        if settings.allow_unauthenticated_public_bind:
            anonymous = replace(root, role="user")

            def _anonymous_non_admin() -> Caller:
                return Caller(anonymous)

            return _anonymous_non_admin

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
    # Only `MimicError` (and its `InvalidRequest` subclass, which also
    # inherits `ValueError` for validation helpers called outside a request)
    # is mapped to an HTTP response here. A plain `ValueError` raised
    # anywhere else — soundfile, numpy, a genuine bug — is deliberately left
    # unhandled so FastAPI's default 500 path takes it, instead of a blanket
    # handler turning every server fault into a client-fault 400.
    @app.exception_handler(MimicError)
    async def _handle(_: Request, exc: MimicError) -> JSONResponse:
        headers = _CHALLENGE if exc.status == 401 else None
        return JSONResponse(status_code=exc.status, content=exc.payload(), headers=headers)
