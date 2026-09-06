"""Shared request-building and error-translation logic."""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator

from mimic.errors import (
    MimicAPIError,
    MimicAuthError,
    MimicConnectionError,
    MimicForbiddenError,
    MimicNotFoundError,
    MimicQuotaError,
    MimicTimeoutError,
    MimicValidationError,
)

_DNS_MARKERS = ("name or service not known", "nodename nor servname", "temporary failure in name")


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
        raise MimicAuthError(response.status_code, detail, body=body)
    if response.status_code == 403:
        raise MimicForbiddenError(response.status_code, detail, body=body)
    if response.status_code == 404:
        raise MimicNotFoundError(response.status_code, detail, body=body)
    if response.status_code == 429:
        raise MimicQuotaError(
            response.status_code,
            detail,
            used=int(body.get("used", 0)),
            limit=int(body.get("limit", 0)),
            resets_at=str(body.get("resets_at", "")),
            body=body,
        )
    if 400 <= response.status_code < 500:
        raise MimicValidationError(response.status_code, detail, body=body)
    raise MimicAPIError(response.status_code, detail, body=body)


def _connect_reason(exc: httpx.TransportError) -> str:
    """Phrase a transport failure the way a person would describe it.

    httpx surfaces the OS error verbatim ("[Errno 111] Connection refused"),
    which is noise to anyone who is not debugging sockets.
    """
    raw = str(exc).strip()
    lowered = raw.lower()
    if any(marker in lowered for marker in _DNS_MARKERS):
        return "unknown host (DNS lookup failed)"
    if "certificate" in lowered or "ssl" in lowered:
        return f"TLS handshake failed: {raw}"
    without_errno = re.sub(r"^\[Errno -?\d+\]\s*", "", raw)
    return (without_errno or "connection failed").lower()


@contextmanager
def map_transport_errors(server_url: str) -> Iterator[None]:
    """Translate httpx transport failures into the mimic error hierarchy.

    Callers of this library should never have to catch an httpx exception to
    handle "the server is down".
    """
    try:
        yield
    except httpx.TimeoutException as e:
        raise MimicTimeoutError(server_url, "timed out waiting for a response") from e
    except httpx.TransportError as e:
        raise MimicConnectionError(server_url, _connect_reason(e)) from e
