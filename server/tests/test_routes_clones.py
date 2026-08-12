"""Permission-aware clone management routes: register, list, delete, publish, grant."""

from __future__ import annotations

from conftest import _auth, _register, _wav
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from mimic_server.app import build_app
from mimic_server.config import Settings

_EXEMPT_UNAUTHENTICATED_PATHS = frozenset(
    {"/health", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
)


def test_register_is_owned_by_the_caller(env):
    client, tokens, _ = env
    assert _register(client, tokens, "dave", "warm").status_code == 200
    body = client.get("/clone/voices", headers=_auth(tokens, "dave")).json()
    assert body["voices"] == ["dave/warm"]
    assert body["detail"][0] == {
        "name": "warm",
        "qualified": "dave/warm",
        "owner": "dave",
        "visibility": "private",
        "mine": True,
    }


def test_others_cannot_see_a_private_voice(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert client.get("/clone/voices", headers=_auth(tokens, "erin")).json()["voices"] == []


def test_admin_sees_every_voice(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert client.get("/clone/voices", headers=_auth(tokens, "root")).json()["voices"] == [
        "dave/warm"
    ]


def test_publish_then_everyone_sees_it(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.patch(
        "/clone/voices/warm", headers=_auth(tokens, "dave"), json={"visibility": "public"}
    )
    assert r.status_code == 200
    assert client.get("/clone/voices", headers=_auth(tokens, "erin")).json()["voices"] == [
        "dave/warm"
    ]


def test_grant_and_revoke(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert (
        client.post(
            "/clone/voices/warm/grants", headers=_auth(tokens, "dave"), json={"grantee": "erin"}
        ).status_code
        == 200
    )
    assert client.get("/clone/voices", headers=_auth(tokens, "erin")).json()["voices"] == [
        "dave/warm"
    ]
    assert (
        client.delete("/clone/voices/warm/grants/erin", headers=_auth(tokens, "dave")).status_code
        == 200
    )
    assert client.get("/clone/voices", headers=_auth(tokens, "erin")).json()["voices"] == []


def test_non_owner_grant_is_403(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    client.patch("/clone/voices/warm", headers=_auth(tokens, "dave"), json={"visibility": "public"})
    r = client.post(
        "/clone/voices/dave/warm/grants", headers=_auth(tokens, "erin"), json={"grantee": "erin"}
    )
    assert r.status_code == 403


def test_delete_someone_elses_private_voice_is_404(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert (
        client.delete("/clone/voices/dave/warm", headers=_auth(tokens, "erin")).status_code == 404
    )


def test_admin_can_delete_any_voice(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert (
        client.delete("/clone/voices/dave/warm", headers=_auth(tokens, "root")).status_code == 200
    )


def test_upload_blocked_when_can_upload_false(env):
    client, tokens, keys = env
    keys.update("dave", can_upload=False)
    r = _register(client, tokens, "dave", "warm")
    assert r.status_code == 403
    assert r.json()["error"] == "upload_not_allowed"


def test_max_voices_returns_409(env):
    client, tokens, keys = env
    keys.update("dave", max_voices=1)
    _register(client, tokens, "dave", "one")
    r = _register(client, tokens, "dave", "two")
    assert r.status_code == 409
    assert r.json()["error"] == "voice_limit_reached"


def test_no_endpoint_serves_reference_audio(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    for path in ("/clone/voices/dave/warm", "/clone/voices/dave/warm/audio.wav"):
        r = client.get(path, headers=_auth(tokens, "root"))
        assert r.status_code in (404, 405)


def test_migrated_voice_is_reachable_after_bootstrap(tmp_path, fake_backend):
    """A pre-existing flat `reference/<name>/` clone must survive the move to
    `reference/<root>/<name>/` that bootstrap() performs at app construction —
    otherwise every voice a live server already had would go dark on upgrade."""
    reference_dir = tmp_path / "reference"
    legacy = reference_dir / "jim"
    legacy.mkdir(parents=True)
    (legacy / "audio.wav").write_bytes(_wav())
    (legacy / "text.txt").write_text("hello from jim")

    settings = Settings(
        reference_dir=reference_dir,
        db_path=tmp_path / "mimic.db",
        api_token="root-token",  # noqa: S106
        root_label="jim",
    )
    client = TestClient(build_app(settings, backend_factory=lambda _s: fake_backend))
    headers = {"Authorization": "Bearer root-token"}

    body = client.get("/clone/voices", headers=headers).json()
    assert body["voices"] == ["jim/jim"]

    r = client.post("/clone/tts", headers=headers, data={"text": "hi", "name": "jim"})
    assert r.status_code == 200
    fake_backend.synth_clone.assert_called_once()
    assert fake_backend.synth_clone.call_args.kwargs["name"] == "jim/jim"


def test_every_route_but_health_requires_a_caller(env):
    client, _, _ = env
    app = client.app
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in _EXEMPT_UNAUTHENTICATED_PATHS:
            continue
        dep_names = {dep.name for dep in route.dependant.dependencies}
        assert "caller" in dep_names, f"{route.path} has no caller dependency: {dep_names}"
