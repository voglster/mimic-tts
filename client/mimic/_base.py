"""Shared request-building and error-translation logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

from mimic.errors import (
    MimicAPIError,
    MimicAuthError,
    MimicNotFoundError,
    MimicValidationError,
)


@dataclass
class RequestSpec:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    data: dict[str, Any] | None = None
    files: dict[str, Any] | None = None


def build_request_spec(
    *,
    base_url: str,
    method: str,
    path: str,
    token: str | None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> RequestSpec:
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")
    url = base_url.rstrip("/") + path
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return RequestSpec(method=method, url=url, headers=headers, data=data, files=files)


def _extract_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
    except Exception:  # noqa: S110
        pass
    return response.text or response.reason_phrase or ""


def raise_for_response(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = _extract_detail(response)
    if response.status_code == 401:
        raise MimicAuthError(response.status_code, detail)
    if response.status_code == 404:
        raise MimicNotFoundError(response.status_code, detail)
    if 400 <= response.status_code < 500:
        raise MimicValidationError(response.status_code, detail)
    raise MimicAPIError(response.status_code, detail)
