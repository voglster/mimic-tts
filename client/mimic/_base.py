"""Shared request-building and error-translation logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

from mimic.errors import (
    MimicAPIError,
    MimicAuthError,
    MimicForbiddenError,
    MimicNotFoundError,
    MimicQuotaError,
    MimicValidationError,
)


@dataclass
class RequestSpec:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    data: dict[str, Any] | None = None
    files: dict[str, Any] | None = None
    json: dict[str, Any] | None = None


def build_request_spec(
    *,
    base_url: str,
    method: str,
    path: str,
    token: str | None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> RequestSpec:
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")
    url = base_url.rstrip("/") + path
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return RequestSpec(method=method, url=url, headers=headers, data=data, files=files, json=json)


def _body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def raise_for_response(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    body = _body(response)
    detail = str(body.get("detail") or response.text or response.reason_phrase or "")
    if response.status_code == 401:
        raise MimicAuthError(response.status_code, detail)
    if response.status_code == 403:
        raise MimicForbiddenError(response.status_code, detail)
    if response.status_code == 404:
        raise MimicNotFoundError(response.status_code, detail)
    if response.status_code == 429:
        raise MimicQuotaError(
            response.status_code,
            detail,
            used=int(body.get("used", 0)),
            limit=int(body.get("limit", 0)),
            resets_at=str(body.get("resets_at", "")),
        )
    if 400 <= response.status_code < 500:
        raise MimicValidationError(response.status_code, detail)
    raise MimicAPIError(response.status_code, detail)
