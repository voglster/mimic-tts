import json

import httpx
import pytest
from mimic import AsyncClient
from mimic.errors import MimicAuthError, MimicConnectionError


def _wav_bytes() -> bytes:
    return b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 1000


def _recording_client(payload: object) -> tuple[AsyncClient, list[dict[str, object]]]:
    """An AsyncClient wired to a transport that records every request and
    always replies with `payload`."""
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
    return AsyncClient(server_url="http://x", transport=mt), calls


@pytest.fixture
def transport():
    routes: dict[tuple[str, str], httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in routes:
            return routes[key]
        return httpx.Response(404, json={"detail": f"unmocked {key}"})

    return httpx.MockTransport(handler), routes


@pytest.mark.asyncio
async def test_health(transport):
    mt, routes = transport
    routes[("GET", "/health")] = httpx.Response(200, json={"status": "ok"})
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        result = await c.health()
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_list_voices(transport):
    mt, routes = transport
    routes[("GET", "/voices")] = httpx.Response(
        200,
        json={"voices": [{"name": "Ryan", "language": "English"}]},
    )
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        voices = await c.list_voices()
    assert voices[0]["name"] == "Ryan"


@pytest.mark.asyncio
async def test_tts(transport):
    mt, routes = transport
    routes[("POST", "/tts")] = httpx.Response(
        200,
        content=_wav_bytes(),
        headers={"content-type": "audio/wav"},
    )
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        audio = await c.tts("hi")
    assert audio.startswith(b"RIFF")


@pytest.mark.asyncio
async def test_clone_register(transport, tmp_path):
    mt, routes = transport
    routes[("POST", "/clone/register")] = httpx.Response(
        200,
        json={"status": "ok", "name": "alice"},
    )
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        ref = tmp_path / "ref.wav"
        ref.write_bytes(_wav_bytes())
        result = await c.clone_register("alice", ref, "transcript")
    assert result["name"] == "alice"


@pytest.mark.asyncio
async def test_clone_tts(transport):
    mt, routes = transport
    routes[("POST", "/clone/tts")] = httpx.Response(
        200,
        content=_wav_bytes(),
        headers={"content-type": "audio/wav"},
    )
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        audio = await c.clone_tts("alice", "hi")
    assert audio.startswith(b"RIFF")


@pytest.mark.asyncio
async def test_401_raises_auth_error(transport):
    mt, routes = transport
    routes[("GET", "/voices")] = httpx.Response(401, json={"detail": "no token"})
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        with pytest.raises(MimicAuthError):
            await c.list_voices()


@pytest.mark.asyncio
async def test_token_in_header():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"voices": []})

    mt = httpx.MockTransport(handler)
    async with AsyncClient(server_url="http://x", token="shhh", transport=mt) as c:  # noqa: S106
        await c.list_voices()
    assert seen["auth"] == "Bearer shhh"


@pytest.mark.asyncio
async def test_whoami_hits_me():
    client, calls = _recording_client({"label": "dave", "role": "user"})
    result = await client.whoami()
    assert result["label"] == "dave"
    assert calls[-1]["method"] == "GET"
    assert calls[-1]["url"].endswith("/me")


@pytest.mark.asyncio
async def test_create_key_posts_json_and_returns_the_token():
    client, calls = _recording_client({"label": "dave", "token": "mk_abc"})
    result = await client.create_key("dave", max_voices=2, daily_char_quota=100)
    assert result["token"] == "mk_abc"  # noqa: S105
    assert calls[-1]["json"] == {"label": "dave", "max_voices": 2, "daily_char_quota": 100}


@pytest.mark.asyncio
async def test_create_key_omits_unset_fields():
    client, calls = _recording_client({"label": "dave", "token": "mk_abc"})
    await client.create_key("dave")
    assert calls[-1]["json"] == {"label": "dave"}


@pytest.mark.asyncio
async def test_grant_voice_targets_the_qualified_path():
    client, calls = _recording_client({"status": "ok"})
    await client.grant_voice("jim/piper", "dave")
    assert calls[-1]["url"].endswith("/clone/voices/jim/piper/grants")
    assert calls[-1]["json"] == {"grantee": "dave"}


@pytest.mark.asyncio
async def test_revoke_key_passes_purge():
    client, calls = _recording_client({"status": "ok"})
    await client.revoke_key("dave", purge=True)
    assert calls[-1]["method"] == "DELETE"
    assert "purge=true" in calls[-1]["url"]


@pytest.mark.asyncio
async def test_revoke_key_without_purge_omits_the_query_param():
    client, calls = _recording_client({"status": "ok"})
    await client.revoke_key("dave")
    assert calls[-1]["method"] == "DELETE"
    assert "purge" not in calls[-1]["url"]


@pytest.mark.asyncio
async def test_set_visibility_patches():
    client, calls = _recording_client({"status": "ok", "visibility": "public"})
    result = await client.set_visibility("warm", "public")
    assert result["visibility"] == "public"
    assert calls[-1]["method"] == "PATCH"
    assert calls[-1]["json"] == {"visibility": "public"}


@pytest.mark.asyncio
async def test_revoke_voice_grant_targets_the_qualified_path():
    client, calls = _recording_client({"status": "ok"})
    await client.revoke_voice_grant("jim/piper", "dave")
    assert calls[-1]["method"] == "DELETE"
    assert calls[-1]["url"].endswith("/clone/voices/jim/piper/grants/dave")


@pytest.mark.asyncio
async def test_list_clone_detail_returns_the_detail_array():
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
    detail = await client.list_clone_detail()
    assert detail[0]["owner"] == "dave"


@pytest.mark.asyncio
async def test_list_clone_detail_tolerates_an_older_server():
    """A server predating `detail` still answers list_clones(); detail is empty."""
    client, _ = _recording_client({"voices": ["warm"]})
    assert await client.list_clone_detail() == []


@pytest.mark.asyncio
async def test_list_keys_unwraps_the_keys_array():
    client, calls = _recording_client({"keys": [{"label": "dave"}]})
    assert await client.list_keys() == [{"label": "dave"}]
    assert calls[-1]["method"] == "GET"
    assert calls[-1]["url"].endswith("/admin/keys")


@pytest.mark.asyncio
async def test_update_key_sends_only_the_passed_fields():
    client, calls = _recording_client({"label": "dave", "enabled": False})
    await client.update_key("dave", enabled=False)
    assert calls[-1]["method"] == "PATCH"
    assert calls[-1]["url"].endswith("/admin/keys/dave")
    assert calls[-1]["json"] == {"enabled": False}


@pytest.mark.asyncio
async def test_admin_usage_builds_the_query_string():
    client, calls = _recording_client({"totals": [], "events": []})
    await client.admin_usage(key="dave", since="2026-01-01", limit=10)
    url = calls[-1]["url"]
    assert calls[-1]["method"] == "GET"
    assert url.startswith("http://x/admin/usage?")
    assert "key=dave" in url
    assert "since=2026-01-01" in url
    assert "limit=10" in url


@pytest.mark.asyncio
async def test_admin_usage_defaults_limit_to_100():
    client, calls = _recording_client({"totals": [], "events": []})
    await client.admin_usage()
    assert "limit=100" in calls[-1]["url"]
    assert "key=" not in calls[-1]["url"]
    assert "since=" not in calls[-1]["url"]


@pytest.mark.asyncio
async def test_admin_voices_unwraps_the_voices_array():
    client, calls = _recording_client({"voices": [{"qualified": "dave/warm"}]})
    assert await client.admin_voices() == [{"qualified": "dave/warm"}]
    assert calls[-1]["url"].endswith("/admin/voices")


@pytest.mark.asyncio
async def test_create_key_sends_json_body_through_the_transport():
    """Closes the Task 1 gap: prove `json=` actually reaches httpx via
    AsyncClient, not just that build_request_spec returned it."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"label": "dave", "token": "mk_abc"})

    mt = httpx.MockTransport(handler)
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        await c.create_key("dave", role="admin")
    assert seen["content_type"].startswith("application/json")
    assert seen["body"] == {"label": "dave", "role": "admin"}


async def test_async_connect_error_becomes_mimic_connection_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused")

    mt = httpx.MockTransport(handler)
    async with AsyncClient(server_url="https://tts.example.com", transport=mt) as c:
        with pytest.raises(MimicConnectionError) as excinfo:
            await c.health()
    assert excinfo.value.server_url == "https://tts.example.com"
    assert excinfo.value.reason == "connection refused"


async def test_async_audio_requests_also_translate_transport_errors():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused")

    mt = httpx.MockTransport(handler)
    async with AsyncClient(server_url="https://tts.example.com", transport=mt) as c:
        with pytest.raises(MimicConnectionError):
            await c.tts("hello")
