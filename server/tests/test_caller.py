from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from mimic_server.auth import install_error_handler, make_caller_dependency
from mimic_server.bootstrap import bootstrap
from mimic_server.config import Settings
from mimic_server.errors import Forbidden
from mimic_server.identity import Caller


@pytest.fixture
def env(tmp_path):
    def build(**kw):
        settings = Settings(
            reference_dir=tmp_path / "reference", db_path=tmp_path / "mimic.db", **kw
        )
        result = bootstrap(settings)
        dependency = make_caller_dependency(settings, result.keys, result.root)
        app = FastAPI()
        install_error_handler(app)

        @app.get("/who")
        def who(caller: Annotated[Caller, Depends(dependency)]) -> dict[str, object]:
            return {"label": caller.label, "admin": caller.is_admin}

        @app.get("/boom")
        def boom() -> None:
            raise Forbidden("nope")

        return TestClient(app), result

    return build


def test_dev_mode_resolves_to_root(env):
    client, result = env()
    body = client.get("/who").json()
    assert body == {"label": result.root.label, "admin": True}


def test_missing_token_is_401_with_challenge(env):
    client, _ = env(api_token="s3cret")  # noqa: S106
    r = client.get("/who")
    assert r.status_code == 401
    assert "Bearer" in r.headers["WWW-Authenticate"]


def test_root_token_authenticates_as_admin(env):
    client, _ = env(api_token="s3cret")  # noqa: S106
    r = client.get("/who", headers={"Authorization": "Bearer s3cret"})
    assert r.json()["admin"] is True


def test_minted_key_authenticates_as_itself(env):
    client, result = env(api_token="s3cret")  # noqa: S106
    _, token = result.keys.create("dave")
    body = client.get("/who", headers={"Authorization": f"Bearer {token}"}).json()
    assert body == {"label": "dave", "admin": False}


def test_revoked_key_is_rejected(env):
    client, result = env(api_token="s3cret")  # noqa: S106
    _, token = result.keys.create("dave")
    result.keys.update("dave", enabled=False)
    assert client.get("/who", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_successful_request_records_last_used(env):
    client, result = env(api_token="s3cret")  # noqa: S106
    _, token = result.keys.create("dave")
    client.get("/who", headers={"Authorization": f"Bearer {token}"})
    assert result.keys.get_by_label("dave").last_used_at is not None


def test_domain_errors_map_to_status_and_payload(env):
    client, _ = env()
    r = client.get("/boom")
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "detail": "nope"}
