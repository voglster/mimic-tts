"""Synchronous client for the mimic-tts server."""
from __future__ import annotations

import os
from io import BufferedReader
from pathlib import Path
from typing import Any

import httpx

from mimic._base import build_request_spec, raise_for_response


class Client:
    """Sync client. Use as a context manager to ensure the transport closes."""

    def __init__(
        self,
        server_url: str | None = None,
        token: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = (server_url or os.environ.get("MIMIC_SERVER_URL")
                          or "http://localhost:8000")
        self._token = token if token is not None else os.environ.get("MIMIC_API_TOKEN")
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        spec = build_request_spec(
            base_url=self._base_url, method=method, path=path,
            token=self._token, **kwargs,
        )
        r = self._http.request(spec.method, spec.url, headers=spec.headers,
                               data=spec.data, files=spec.files)
        raise_for_response(r)
        return r.json()

    def _request_audio(self, method: str, path: str, **kwargs: Any) -> bytes:
        spec = build_request_spec(
            base_url=self._base_url, method=method, path=path,
            token=self._token, **kwargs,
        )
        r = self._http.request(spec.method, spec.url, headers=spec.headers,
                               data=spec.data, files=spec.files)
        raise_for_response(r)
        return r.content

    def health(self) -> dict[str, Any]:
        return self._request_json("GET", "/health")

    def list_voices(self) -> list[dict[str, str]]:
        return self._request_json("GET", "/voices")["voices"]

    def list_clones(self) -> list[str]:
        return self._request_json("GET", "/clone/voices")["voices"]

    def tts(
        self, text: str, *, language: str = "English",
        speaker: str = "Ryan", instruct: str = "",
    ) -> bytes:
        return self._request_audio(
            "POST", "/tts",
            data={"text": text, "language": language,
                  "speaker": speaker, "instruct": instruct},
        )

    def tts_to_file(self, text: str, out: Path | str, **kwargs: Any) -> Path:
        audio = self.tts(text, **kwargs)
        out_path = Path(out)
        out_path.write_bytes(audio)
        return out_path

    def clone_register(
        self, name: str, audio: Path | str | bytes | BufferedReader, transcript: str,
    ) -> dict[str, str]:
        files = {"ref_audio": _as_upload(audio)}
        return self._request_json(
            "POST", "/clone/register",
            data={"name": name, "ref_text": transcript}, files=files,
        )

    def clone_tts(
        self, name: str, text: str, *, language: str = "English",
    ) -> bytes:
        return self._request_audio(
            "POST", "/clone/tts",
            data={"text": text, "language": language, "name": name},
        )

    def clone_oneshot(
        self, text: str, audio: Path | str | bytes | BufferedReader,
        transcript: str, *, language: str = "English",
    ) -> bytes:
        files = {"ref_audio": _as_upload(audio)}
        return self._request_audio(
            "POST", "/clone/oneshot",
            data={"text": text, "language": language, "ref_text": transcript},
            files=files,
        )


def _as_upload(audio: Path | str | bytes | BufferedReader) -> tuple[str, Any, str]:
    """Normalize audio inputs to a (filename, fileobj-or-bytes, content-type) tuple."""
    if isinstance(audio, (str, Path)):
        path = Path(audio)
        return (path.name, path.read_bytes(), "audio/wav")
    if isinstance(audio, bytes):
        return ("ref.wav", audio, "audio/wav")
    return ("ref.wav", audio, "audio/wav")
