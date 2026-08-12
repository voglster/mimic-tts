import httpx
import pytest
from mimic._base import RequestSpec, build_request_spec, raise_for_response
from mimic.errors import (
    MimicAPIError,
    MimicAuthError,
    MimicForbiddenError,
    MimicNotFoundError,
    MimicQuotaError,
)


def test_build_get_request_no_auth():
    spec = build_request_spec(
        base_url="http://localhost:8000",
        method="GET",
        path="/voices",
        token=None,
    )
    assert spec.method == "GET"
    assert spec.url == "http://localhost:8000/voices"
    assert "authorization" not in {k.lower() for k in spec.headers}


def test_build_request_with_token_adds_bearer_header():
    spec = build_request_spec(
        base_url="http://localhost:8000",
        method="POST",
        path="/tts",
        token="shhh",  # noqa: S106
        data={"text": "hello"},
    )
    assert spec.headers["Authorization"] == "Bearer shhh"
    assert spec.data == {"text": "hello"}


def test_base_url_strips_trailing_slash():
    spec = build_request_spec(
        base_url="http://localhost:8000/",
        method="GET",
        path="/health",
        token=None,
    )
    assert spec.url == "http://localhost:8000/health"


def test_path_must_start_with_slash():
    with pytest.raises(ValueError, match="path must start with"):
        build_request_spec(
            base_url="http://localhost:8000",
            method="GET",
            path="health",
            token=None,
        )


def test_files_field_passed_through():
    spec = build_request_spec(
        base_url="http://localhost:8000",
        method="POST",
        path="/clone/register",
        token=None,
        data={"name": "alice", "ref_text": "hi"},
        files={"ref_audio": ("ref.wav", b"RIFF...", "audio/wav")},
    )
    assert spec.files == {"ref_audio": ("ref.wav", b"RIFF...", "audio/wav")}


def test_raise_for_response_401_raises_auth_error():
    response = httpx.Response(401, json={"detail": "missing bearer token"})
    with pytest.raises(MimicAuthError) as exc_info:
        raise_for_response(response)
    assert "missing bearer token" in str(exc_info.value)


def test_raise_for_response_404_raises_not_found():
    response = httpx.Response(404, json={"detail": "no voice 'alice' registered"})
    with pytest.raises(MimicNotFoundError):
        raise_for_response(response)


def test_raise_for_response_5xx_raises_generic_api_error():
    response = httpx.Response(500, text="boom")
    with pytest.raises(MimicAPIError) as exc_info:
        raise_for_response(response)
    assert exc_info.value.status_code == 500


def test_raise_for_response_2xx_does_nothing():
    response = httpx.Response(200, json={"ok": True})
    raise_for_response(response)  # should not raise


def test_request_spec_is_a_dataclass():
    spec = RequestSpec(method="GET", url="http://x/y", headers={}, data=None, files=None)
    assert spec.method == "GET"


def test_spec_carries_a_json_body():
    spec = build_request_spec(
        base_url="http://x",
        method="POST",
        path="/admin/keys",
        token="t",  # noqa: S106
        json={"label": "dave"},
    )
    assert spec.json == {"label": "dave"}
    assert spec.data is None


def test_403_raises_forbidden():
    response = httpx.Response(403, json={"error": "forbidden", "detail": "admin key required"})
    with pytest.raises(MimicForbiddenError) as exc:
        raise_for_response(response)
    assert "admin key required" in str(exc.value)


def test_429_raises_quota_error_with_fields():
    response = httpx.Response(
        429,
        json={
            "error": "quota_exceeded",
            "detail": "daily character quota exceeded (95/100)",
            "used": 95,
            "limit": 100,
            "resets_at": "2026-08-12T00:00:00+00:00",
        },
    )
    with pytest.raises(MimicQuotaError) as exc:
        raise_for_response(response)
    assert (exc.value.used, exc.value.limit) == (95, 100)
    assert exc.value.resets_at.startswith("2026-08-12")


def test_error_detail_reads_the_new_error_shape():
    """The server returns {"error", "detail"}; older routes returned {"detail"}."""
    response = httpx.Response(404, json={"error": "voice_not_found", "detail": "no voice 'x'"})
    with pytest.raises(MimicNotFoundError) as exc:
        raise_for_response(response)
    assert "no voice 'x'" in str(exc.value)
