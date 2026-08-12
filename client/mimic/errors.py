"""Exception hierarchy for mimic-tts client errors."""

from __future__ import annotations

from typing import Any


class MimicError(Exception):
    """Base class for all mimic-tts client errors."""


class MimicAPIError(MimicError):
    """Server returned a non-2xx response."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        # Full JSON body, when available — carries structured extras (e.g. a
        # 422's list-shaped `detail`, or a 409's `candidates`) that a rendered
        # one-line message alone can't express.
        self.body = body or {}


class MimicAuthError(MimicAPIError):
    """401: missing or invalid bearer token."""


class MimicNotFoundError(MimicAPIError):
    """404: requested resource (e.g. clone voice) does not exist."""


class MimicValidationError(MimicAPIError):
    """4xx other than 401/404: request was rejected as invalid."""


class MimicForbiddenError(MimicAPIError):
    """403: the key authenticated but is not allowed to do this."""


class MimicQuotaError(MimicAPIError):
    """429: the key's daily character quota is exhausted."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        used: int = 0,
        limit: int = 0,
        resets_at: str = "",
        body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code, message, body=body)
        self.used = used
        self.limit = limit
        self.resets_at = resets_at
