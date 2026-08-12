"""Domain errors. Route handlers raise these; one FastAPI exception handler
maps them to responses, so HTTP status choices live in exactly one place."""

from __future__ import annotations

from typing import Any


class MimicError(Exception):
    status: int = 400
    code: str = "error"

    def __init__(self, message: str, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra or {}

    def payload(self) -> dict[str, Any]:
        return {"error": self.code, "detail": self.message, **self.extra}


class Unauthorized(MimicError):  # noqa: N818
    status = 401
    code = "unauthorized"


class Forbidden(MimicError):  # noqa: N818
    status = 403
    code = "forbidden"


class UploadNotAllowed(Forbidden):
    code = "upload_not_allowed"


class VoiceNotFound(MimicError):  # noqa: N818
    status = 404
    code = "voice_not_found"


class AmbiguousVoice(MimicError):  # noqa: N818
    status = 409
    code = "ambiguous_voice"


class VoiceLimitReached(MimicError):  # noqa: N818
    status = 409
    code = "voice_limit_reached"


class LabelInUse(MimicError):  # noqa: N818
    status = 409
    code = "label_in_use"


class QuotaExceeded(MimicError):  # noqa: N818
    status = 429
    code = "quota_exceeded"


class InvalidRequest(MimicError):  # noqa: N818
    status = 400
    code = "invalid_request"


class ReservedName(MimicError):  # noqa: N818
    status = 400
    code = "reserved_name"
