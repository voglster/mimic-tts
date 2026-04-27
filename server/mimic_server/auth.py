"""Optional bearer-token auth dependency."""
from __future__ import annotations

import secrets
from typing import Callable

from fastapi import Header, HTTPException, status

from mimic_server.config import Settings


def require_token(settings: Settings) -> Callable[..., None]:
    """Return a dependency. If no token is configured, dependency is a no-op."""

    if not settings.auth_required:
        def _noop() -> None:
            return None
        return _noop

    expected = settings.api_token or ""

    def _check(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing bearer token",
                headers={"WWW-Authenticate": 'Bearer realm="mimic"'},
            )
        token = authorization.removeprefix("Bearer ").strip()
        if not secrets.compare_digest(token, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": 'Bearer realm="mimic"'},
            )

    return _check
