import httpx
import pytest

from mimic import AsyncClient
from mimic.errors import MimicAuthError


def _wav_bytes() -> bytes:
    return b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 1000


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
        200, json={"voices": [{"name": "Ryan", "language": "English"}]},
    )
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        voices = await c.list_voices()
    assert voices[0]["name"] == "Ryan"


@pytest.mark.asyncio
async def test_tts(transport):
    mt, routes = transport
    routes[("POST", "/tts")] = httpx.Response(
        200, content=_wav_bytes(), headers={"content-type": "audio/wav"},
    )
    async with AsyncClient(server_url="http://x", transport=mt) as c:
        audio = await c.tts("hi")
    assert audio.startswith(b"RIFF")


@pytest.mark.asyncio
async def test_clone_register(transport, tmp_path):
    mt, routes = transport
    routes[("POST", "/clone/register")] = httpx.Response(
        200, json={"status": "ok", "name": "alice"},
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
        200, content=_wav_bytes(), headers={"content-type": "audio/wav"},
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
    async with AsyncClient(server_url="http://x", token="shhh", transport=mt) as c:
        await c.list_voices()
    assert seen["auth"] == "Bearer shhh"
