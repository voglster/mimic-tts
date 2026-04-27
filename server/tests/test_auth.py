from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from mimic_server.auth import require_token
from mimic_server.config import Settings


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    def protected(_: None = Depends(require_token(settings))) -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_no_token_configured_allows_anyone():
    settings = Settings(api_token=None)
    client = TestClient(_app(settings))
    assert client.get("/protected").status_code == 200


def test_token_required_rejects_missing_header():
    settings = Settings(api_token="shhh")
    client = TestClient(_app(settings))
    r = client.get("/protected")
    assert r.status_code == 401
    assert "Bearer" in r.headers.get("WWW-Authenticate", "")


def test_token_required_rejects_wrong_token():
    settings = Settings(api_token="shhh")
    client = TestClient(_app(settings))
    r = client.get("/protected", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_token_required_accepts_correct_token():
    settings = Settings(api_token="shhh")
    client = TestClient(_app(settings))
    r = client.get("/protected", headers={"Authorization": "Bearer shhh"})
    assert r.status_code == 200
