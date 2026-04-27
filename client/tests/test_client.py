import httpx
import pytest
from mimic import Client
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


def test_health(transport):
    mt, routes = transport
    routes[("GET", "/health")] = httpx.Response(
        200,
        json={"status": "ok", "models_loaded": [], "registered_voices": []},
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
