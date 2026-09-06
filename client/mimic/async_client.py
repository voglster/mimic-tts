"""Asynchronous client for the mimic-tts server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx

if TYPE_CHECKING:
    from io import BufferedReader

from mimic._base import build_request_spec, map_transport_errors, raise_for_response
from mimic.client import _as_upload


class AsyncClient:
    """Async client. Use as `async with AsyncClient(...) as c:`."""

    def __init__(
        self,
        server_url: str | None = None,
        token: str | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = server_url or os.environ.get("MIMIC_SERVER_URL") or "http://localhost:8000"
        self._token = token if token is not None else os.environ.get("MIMIC_API_TOKEN")
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)  # type: ignore[arg-type]

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        spec = build_request_spec(
            base_url=self._base_url,
            method=method,
            path=path,
            token=self._token,
            **kwargs,
        )
        with map_transport_errors(self._base_url):
            r = await self._http.request(
                spec.method,
                spec.url,
                headers=spec.headers,
                data=spec.data,
                files=spec.files,
                json=spec.json,
            )
        raise_for_response(r)
        return r.json()

    async def _request_audio(self, method: str, path: str, **kwargs: Any) -> bytes:
        spec = build_request_spec(
            base_url=self._base_url,
            method=method,
            path=path,
            token=self._token,
            **kwargs,
        )
        with map_transport_errors(self._base_url):
            r = await self._http.request(
                spec.method,
                spec.url,
                headers=spec.headers,
                data=spec.data,
                files=spec.files,
                json=spec.json,
            )
        raise_for_response(r)
        return r.content

    async def health(self) -> dict[str, Any]:
        return await self._request_json("GET", "/health")

    async def list_voices(self) -> list[dict[str, str]]:
        return (await self._request_json("GET", "/voices"))["voices"]

    async def list_clones(self) -> list[str]:
        return (await self._request_json("GET", "/clone/voices"))["voices"]

    async def list_clone_detail(self) -> list[dict[str, Any]]:
        return (await self._request_json("GET", "/clone/voices")).get("detail", [])

    async def whoami(self) -> dict[str, Any]:
        return await self._request_json("GET", "/me")

    async def set_visibility(self, spec: str, visibility: str) -> dict[str, Any]:
        return await self._request_json(
            "PATCH", f"/clone/voices/{spec}", json={"visibility": visibility}
        )

    async def grant_voice(self, spec: str, grantee: str) -> dict[str, Any]:
        return await self._request_json(
            "POST", f"/clone/voices/{spec}/grants", json={"grantee": grantee}
        )

    async def revoke_voice_grant(self, spec: str, grantee: str) -> dict[str, Any]:
        return await self._request_json("DELETE", f"/clone/voices/{spec}/grants/{grantee}")

    async def create_key(self, label: str, **fields: Any) -> dict[str, Any]:
        body = {"label": label, **{k: v for k, v in fields.items() if v is not None}}
        return await self._request_json("POST", "/admin/keys", json=body)

    async def list_keys(self) -> list[dict[str, Any]]:
        return (await self._request_json("GET", "/admin/keys"))["keys"]

    async def update_key(self, label: str, **fields: Any) -> dict[str, Any]:
        body = {k: v for k, v in fields.items() if v is not None}
        return await self._request_json("PATCH", f"/admin/keys/{label}", json=body)

    async def revoke_key(self, label: str, *, purge: bool = False) -> dict[str, Any]:
        suffix = "?purge=true" if purge else ""
        return await self._request_json("DELETE", f"/admin/keys/{label}{suffix}")

    async def admin_usage(
        self, key: str | None = None, since: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if key is not None:
            params["key"] = key
        if since is not None:
            params["since"] = since
        return await self._request_json("GET", f"/admin/usage?{urlencode(params)}")

    async def admin_voices(self) -> list[dict[str, Any]]:
        return (await self._request_json("GET", "/admin/voices"))["voices"]

    async def tts(
        self,
        text: str,
        *,
        language: str = "English",
        speaker: str = "Ryan",
        instruct: str = "",
    ) -> bytes:
        return await self._request_audio(
            "POST",
            "/tts",
            data={"text": text, "language": language, "speaker": speaker, "instruct": instruct},
        )

    async def tts_to_file(self, text: str, out: Path | str, **kwargs: Any) -> Path:
        audio = await self.tts(text, **kwargs)
        out_path = Path(out)
        out_path.write_bytes(audio)
        return out_path

    async def clone_register(
        self,
        name: str,
        audio: Path | str | bytes | BufferedReader,
        transcript: str,
    ) -> dict[str, str]:
        files = {"ref_audio": _as_upload(audio)}
        return await self._request_json(
            "POST",
            "/clone/register",
            data={"name": name, "ref_text": transcript},
            files=files,
        )

    async def clone_tts(
        self,
        name: str,
        text: str,
        *,
        language: str = "English",
    ) -> bytes:
        return await self._request_audio(
            "POST",
            "/clone/tts",
            data={"text": text, "language": language, "name": name},
        )

    async def clone_oneshot(
        self,
        text: str,
        audio: Path | str | bytes | BufferedReader,
        transcript: str,
        *,
        language: str = "English",
    ) -> bytes:
        files = {"ref_audio": _as_upload(audio)}
        return await self._request_audio(
            "POST",
            "/clone/oneshot",
            data={"text": text, "language": language, "ref_text": transcript},
            files=files,
        )
