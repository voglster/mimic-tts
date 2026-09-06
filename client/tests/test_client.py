import json

import httpx
import pytest
from mimic import Client
from mimic.errors import MimicAuthError, MimicConnectionError, MimicTimeoutError


def _wav_bytes() -> bytes:
    return b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 1000


def _recording_client(payload: object) -> tuple[Client, list[dict[str, object]]]:
    """A Client wired to a transport that records every request and always
    replies with `payload`."""
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body_json = None
        body_data = None
        content_type = request.headers.get("content-type", "")
        if request.content:
            if content_type.startswith("application/json"):
                body_json = json.loads(request.content)
            elif content_type.startswith("application/x-www-form-urlencoded"):
                body_data = dict(httpx.QueryParams(request.content.decode()))
        calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "json": body_json,
                "data": body_data,
            }
        )
        return httpx.Response(200, json=payload)

    mt = httpx.MockTransport(handler)
    return Client(server_url="http://x", transport=mt), calls


@pytest.fixture
def transport():
    routes: dict[tuple[str, str], httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in routes:
            return routes[key]
        return httpx.Response(404, json={"detail": f"unmocked {key}"})

    return httpx.MockTransport(handler), routes


def test_health(transport):
    mt, routes = transport
    routes[("GET", "/health")] = httpx.Response(
        200,
        json={"status": "ok", "backend": "chatterbox", "stt_enabled": False},
    )
    c = Client(server_url="http://x", transport=mt)
    assert c.health()["status"] == "ok"


def test_list_voices(transport):
    mt, routes = transport
    routes[("GET", "/voices")] = httpx.Response(
        200,
        json={"voices": [{"name": "Ryan", "language": "English"}]},
    )
    c = Client(server_url="http://x", transport=mt)
    assert c.list_voices() == [{"name": "Ryan", "language": "English"}]


def test_list_clones(transport):
    mt, routes = transport
    routes[("GET", "/clone/voices")] = httpx.Response(
        200,
        json={"voices": ["alice", "bob"]},
    )
    c = Client(server_url="http://x", transport=mt)
    assert c.list_clones() == ["alice", "bob"]


def test_tts_returns_wav_bytes(transport):
    mt, routes = transport
    routes[("POST", "/tts")] = httpx.Response(
        200,
        content=_wav_bytes(),
        headers={"content-type": "audio/wav"},
    )
    c = Client(server_url="http://x", transport=mt)
    audio = c.tts("hello", speaker="Ryan")
    assert audio.startswith(b"RIFF")


def test_tts_to_file_writes_wav(transport, tmp_path):
    mt, routes = transport
    routes[("POST", "/tts")] = httpx.Response(
        200,
        content=_wav_bytes(),
        headers={"content-type": "audio/wav"},
    )
    c = Client(server_url="http://x", transport=mt)
    out = tmp_path / "out.wav"
    c.tts_to_file("hello", out, speaker="Ryan")
    assert out.read_bytes().startswith(b"RIFF")


def test_clone_register_with_path(transport, tmp_path):
    mt, routes = transport
    routes[("POST", "/clone/register")] = httpx.Response(
        200,
        json={"status": "ok", "name": "alice"},
    )
    c = Client(server_url="http://x", transport=mt)
    audio = tmp_path / "ref.wav"
    audio.write_bytes(_wav_bytes())
    result = c.clone_register("alice", audio, "transcript text")
    assert result == {"status": "ok", "name": "alice"}


def test_clone_register_with_bytes(transport):
    mt, routes = transport
    routes[("POST", "/clone/register")] = httpx.Response(
        200,
        json={"status": "ok", "name": "alice"},
    )
    c = Client(server_url="http://x", transport=mt)
    result = c.clone_register("alice", _wav_bytes(), "transcript")
    assert result["name"] == "alice"


def test_clone_tts(transport):
    mt, routes = transport
    routes[("POST", "/clone/tts")] = httpx.Response(
        200,
        content=_wav_bytes(),
        headers={"content-type": "audio/wav"},
    )
    c = Client(server_url="http://x", transport=mt)
    audio = c.clone_tts("alice", "hi")
    assert audio.startswith(b"RIFF")


def test_clone_oneshot(transport, tmp_path):
    mt, routes = transport
    routes[("POST", "/clone/oneshot")] = httpx.Response(
        200,
        content=_wav_bytes(),
        headers={"content-type": "audio/wav"},
    )
    c = Client(server_url="http://x", transport=mt)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(_wav_bytes())
    audio = c.clone_oneshot("hi", ref, "ref transcript")
    assert audio.startswith(b"RIFF")


def test_401_raises_auth_error(transport):
    mt, routes = transport
    routes[("GET", "/voices")] = httpx.Response(401, json={"detail": "no token"})
    c = Client(server_url="http://x", transport=mt)
    with pytest.raises(MimicAuthError):
        c.list_voices()


def test_404_clone_tts_raises_not_found(transport):
    mt, routes = transport
    routes[("POST", "/clone/tts")] = httpx.Response(
        400,
        json={"detail": "no voice 'ghost' registered"},
    )
    c = Client(server_url="http://x", transport=mt)
    from mimic.errors import MimicValidationError

    with pytest.raises(MimicValidationError):
        c.clone_tts("ghost", "hi")


def test_token_passed_in_authorization_header():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"voices": []})

    mt2 = httpx.MockTransport(handler)
    c = Client(server_url="http://x", token="shhh", transport=mt2)  # noqa: S106
    c.list_voices()
    assert seen["auth"] == "Bearer shhh"


def test_context_manager_closes_transport():
    closed = {"v": False}

    class TrackingTransport(httpx.MockTransport):
        def close(self) -> None:
            closed["v"] = True
            super().close()

    t = TrackingTransport(lambda _r: httpx.Response(200, json={"voices": []}))
    with Client(server_url="http://x", transport=t):
        pass
    assert closed["v"] is True


def test_whoami_hits_me():
    client, calls = _recording_client({"label": "dave", "role": "user"})
    assert client.whoami()["label"] == "dave"
    assert calls[-1]["method"] == "GET"
    assert calls[-1]["url"].endswith("/me")


def test_create_key_posts_json_and_returns_the_token():
    client, calls = _recording_client({"label": "dave", "token": "mk_abc"})
    result = client.create_key("dave", max_voices=2, daily_char_quota=100)
    assert result["token"] == "mk_abc"  # noqa: S105
    assert calls[-1]["json"] == {"label": "dave", "max_voices": 2, "daily_char_quota": 100}


def test_create_key_omits_unset_fields():
    client, calls = _recording_client({"label": "dave", "token": "mk_abc"})
    client.create_key("dave")
    assert calls[-1]["json"] == {"label": "dave"}


def test_grant_voice_targets_the_qualified_path():
    client, calls = _recording_client({"status": "ok"})
    client.grant_voice("jim/piper", "dave")
    assert calls[-1]["url"].endswith("/clone/voices/jim/piper/grants")
    assert calls[-1]["json"] == {"grantee": "dave"}


def test_revoke_key_passes_purge():
    client, calls = _recording_client({"status": "ok"})
    client.revoke_key("dave", purge=True)
    assert calls[-1]["method"] == "DELETE"
    assert "purge=true" in calls[-1]["url"]


def test_revoke_key_without_purge_omits_the_query_param():
    client, calls = _recording_client({"status": "ok"})
    client.revoke_key("dave")
    assert calls[-1]["method"] == "DELETE"
    assert "purge" not in calls[-1]["url"]


def test_set_visibility_patches():
    client, calls = _recording_client({"status": "ok", "visibility": "public"})
    assert client.set_visibility("warm", "public")["visibility"] == "public"
    assert calls[-1]["method"] == "PATCH"
    assert calls[-1]["json"] == {"visibility": "public"}


def test_revoke_voice_grant_targets_the_qualified_path():
    client, calls = _recording_client({"status": "ok"})
    client.revoke_voice_grant("jim/piper", "dave")
    assert calls[-1]["method"] == "DELETE"
    assert calls[-1]["url"].endswith("/clone/voices/jim/piper/grants/dave")


def test_list_clone_detail_returns_the_detail_array():
    payload = {
        "voices": ["dave/warm"],
        "detail": [
            {
                "name": "warm",
                "qualified": "dave/warm",
                "owner": "dave",
                "visibility": "private",
                "mine": True,
            }
        ],
    }
    client, _ = _recording_client(payload)
    assert client.list_clone_detail()[0]["owner"] == "dave"


def test_list_clone_detail_tolerates_an_older_server():
    """A server predating `detail` still answers list_clones(); detail is empty."""
    client, _ = _recording_client({"voices": ["warm"]})
    assert client.list_clone_detail() == []


def test_list_keys_unwraps_the_keys_array():
    client, calls = _recording_client({"keys": [{"label": "dave"}]})
    assert client.list_keys() == [{"label": "dave"}]
    assert calls[-1]["method"] == "GET"
    assert calls[-1]["url"].endswith("/admin/keys")


def test_update_key_sends_only_the_passed_fields():
    client, calls = _recording_client({"label": "dave", "enabled": False})
    client.update_key("dave", enabled=False)
    assert calls[-1]["method"] == "PATCH"
    assert calls[-1]["url"].endswith("/admin/keys/dave")
    assert calls[-1]["json"] == {"enabled": False}


def test_admin_usage_builds_the_query_string():
    client, calls = _recording_client({"totals": [], "events": []})
    client.admin_usage(key="dave", since="2026-01-01", limit=10)
    url = calls[-1]["url"]
    assert calls[-1]["method"] == "GET"
    assert url.startswith("http://x/admin/usage?")
    assert "key=dave" in url
    assert "since=2026-01-01" in url
    assert "limit=10" in url


def test_admin_usage_defaults_limit_to_100():
    client, calls = _recording_client({"totals": [], "events": []})
    client.admin_usage()
    assert "limit=100" in calls[-1]["url"]
    assert "key=" not in calls[-1]["url"]
    assert "since=" not in calls[-1]["url"]


def test_admin_voices_unwraps_the_voices_array():
    client, calls = _recording_client({"voices": [{"qualified": "dave/warm"}]})
    assert client.admin_voices() == [{"qualified": "dave/warm"}]
    assert calls[-1]["url"].endswith("/admin/voices")


def test_create_key_sends_json_body_through_the_transport():
    """Closes the Task 1 gap: prove `json=` actually reaches httpx via Client,
    not just that build_request_spec returns it."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"label": "dave", "token": "mk_abc"})

    mt = httpx.MockTransport(handler)
    c = Client(server_url="http://x", transport=mt)
    c.create_key("dave", role="admin")
    assert seen["content_type"].startswith("application/json")
    assert seen["body"] == {"label": "dave", "role": "admin"}


def _failing_client(exc: Exception) -> Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise exc

    return Client(
        server_url="https://tts.example.com",
        transport=httpx.MockTransport(handler),
    )


def test_connect_error_becomes_mimic_connection_error():
    client = _failing_client(httpx.ConnectError("[Errno 111] Connection refused"))
    with pytest.raises(MimicConnectionError) as excinfo:
        client.health()
    assert excinfo.value.server_url == "https://tts.example.com"
    assert excinfo.value.reason == "connection refused"


def test_unknown_host_reports_dns_failure():
    client = _failing_client(httpx.ConnectError("[Errno -2] Name or service not known"))
    with pytest.raises(MimicConnectionError) as excinfo:
        client.health()
    assert "unknown host" in excinfo.value.reason


def test_timeout_becomes_mimic_timeout_error():
    client = _failing_client(httpx.ConnectTimeout("timed out"))
    with pytest.raises(MimicTimeoutError) as excinfo:
        client.health()
    assert isinstance(excinfo.value, MimicConnectionError)


def test_audio_requests_also_translate_transport_errors():
    client = _failing_client(httpx.ConnectError("[Errno 111] Connection refused"))
    with pytest.raises(MimicConnectionError):
        client.tts("hello")
