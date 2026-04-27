"""Exception hierarchy for mimic-tts client errors."""
from __future__ import annotations


class MimicError(Exception):
    """Base class for all mimic-tts client errors."""


class MimicAPIError(MimicError):
    """Server returned a non-2xx response."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class MimicAuthError(MimicAPIError):
    """401: missing or invalid bearer token."""


class MimicNotFoundError(MimicAPIError):
    """404: requested resource (e.g. clone voice) does not exist."""


class MimicValidationError(MimicAPIError):
    """4xx other than 401/404: request was rejected as invalid."""
