"""`/me` and the `/admin/*` surface: minting, listing, revoking keys, and
server-wide usage/voice views."""

from __future__ import annotations

from conftest import _auth, _register


def test_health_is_open_but_reveals_nothing(env):
    client, _, _ = env
    body = client.get("/health").json()
    assert set(body) == {"status", "backend", "stt_enabled"}


def test_me_reports_identity_and_quota(env):
    client, tokens, _ = env
    body = client.get("/me", headers=_auth(tokens, "dave")).json()
    assert body["label"] == "dave"
    assert body["role"] == "user"
    assert body["can_upload"] is True
    assert body["max_voices"] == 5
    assert body["daily_char_quota"] == 50000
    assert body["usage_today"] == {"requests": 0, "chars": 0, "audio_seconds": 0.0}


def test_non_admin_is_forbidden_from_admin_routes(env):
    client, tokens, _ = env
    for path in ("/admin/keys", "/admin/usage", "/admin/voices"):
        assert client.get(path, headers=_auth(tokens, "dave")).status_code == 403


def test_anonymous_is_unauthorized_on_admin_routes(env):
    client, _, _ = env
    for path in ("/admin/keys", "/admin/usage", "/admin/voices"):
        assert client.get(path).status_code == 401


def test_mint_returns_the_token_exactly_once(env):
    client, tokens, _ = env
    r = client.post("/admin/keys", headers=_auth(tokens, "root"), json={"label": "frank"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token.startswith("mk_")
    listing = client.get("/admin/keys", headers=_auth(tokens, "root")).json()["keys"]
    frank = next(k for k in listing if k["label"] == "frank")
    assert "token" not in frank
    assert frank["token_prefix"] == token[3:11]


def test_minted_key_works_immediately(env):
    client, tokens, _ = env
    token = client.post(
        "/admin/keys", headers=_auth(tokens, "root"), json={"label": "frank"}
    ).json()["token"]
    assert (
        client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["label"] == "frank"
    )


def test_duplicate_label_is_409(env):
    client, tokens, _ = env
    client.post("/admin/keys", headers=_auth(tokens, "root"), json={"label": "frank"})
    r = client.post("/admin/keys", headers=_auth(tokens, "root"), json={"label": "frank"})
    assert r.status_code == 409
    assert r.json()["error"] == "label_in_use"


def test_patch_adjusts_quotas(env):
    client, tokens, _ = env
    r = client.patch(
        "/admin/keys/dave", headers=_auth(tokens, "root"), json={"daily_char_quota": 10}
    )
    assert r.status_code == 200
    assert r.json()["daily_char_quota"] == 10


def test_revoke_disables_but_keeps_voices(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    assert client.delete("/admin/keys/dave", headers=_auth(tokens, "root")).status_code == 200
    assert client.get("/me", headers=_auth(tokens, "dave")).status_code == 401
    voices = client.get("/admin/voices", headers=_auth(tokens, "root")).json()["voices"]
    assert [v["qualified"] for v in voices] == ["dave/warm"]


def test_purge_removes_the_key_and_its_voices(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.delete("/admin/keys/dave?purge=true", headers=_auth(tokens, "root"))
    assert r.status_code == 200
    assert client.get("/admin/voices", headers=_auth(tokens, "root")).json()["voices"] == []


def test_purge_removes_reference_audio_from_disk(env, tmp_path):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    voice_dir = tmp_path / "reference" / "dave" / "warm"
    assert voice_dir.exists()
    client.delete("/admin/keys/dave?purge=true", headers=_auth(tokens, "root"))
    assert not voice_dir.exists()


def test_root_key_cannot_be_revoked(env):
    client, tokens, _ = env
    r = client.delete("/admin/keys/root", headers=_auth(tokens, "root"))
    assert r.status_code == 403
    assert client.get("/me", headers=_auth(tokens, "root")).status_code == 200


def test_root_key_cannot_be_purged(env):
    client, tokens, _ = env
    r = client.delete("/admin/keys/root?purge=true", headers=_auth(tokens, "root"))
    assert r.status_code == 403


def test_root_key_cannot_be_disabled_via_patch(env):
    client, tokens, _ = env
    r = client.patch("/admin/keys/root", headers=_auth(tokens, "root"), json={"enabled": False})
    assert r.status_code == 403
    assert client.get("/me", headers=_auth(tokens, "root")).status_code == 200


def test_root_key_cannot_be_demoted_via_patch(env):
    client, tokens, _ = env
    r = client.patch("/admin/keys/root", headers=_auth(tokens, "root"), json={"role": "user"})
    assert r.status_code == 403
    assert client.get("/me", headers=_auth(tokens, "root")).json()["role"] == "admin"


def test_admin_voices_lists_owner_and_grants(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    client.post(
        "/clone/voices/warm/grants", headers=_auth(tokens, "dave"), json={"grantee": "erin"}
    )
    voices = client.get("/admin/voices", headers=_auth(tokens, "root")).json()["voices"]
    assert voices[0]["owner"] == "dave"
    assert voices[0]["grants"] == ["erin"]


def test_admin_usage_reports_per_key_totals(env):
    client, tokens, _ = env
    client.post("/tts", headers=_auth(tokens, "dave"), data={"text": "hello"})
    totals = client.get("/admin/usage", headers=_auth(tokens, "root")).json()["totals"]
    assert {
        "label": "dave",
        "requests": 1,
        "chars": 5,
        "audio_seconds": totals[0]["audio_seconds"],
    } in totals


def test_admin_usage_for_a_fresh_key_is_empty_not_crashing(env):
    client, tokens, _ = env
    r = client.get("/admin/usage", headers=_auth(tokens, "root"), params={"key": "dave"})
    assert r.status_code == 200
    assert r.json() == {"totals": [], "events": []}


def test_grant_to_unknown_grantee_is_404_with_accurate_code(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.post(
        "/clone/voices/warm/grants", headers=_auth(tokens, "dave"), json={"grantee": "nobody"}
    )
    assert r.status_code == 404
    assert r.json()["error"] != "voice_not_found"


def test_revoke_grant_to_unknown_grantee_is_404_with_accurate_code(env):
    client, tokens, _ = env
    _register(client, tokens, "dave", "warm")
    r = client.delete("/clone/voices/warm/grants/nobody", headers=_auth(tokens, "dave"))
    assert r.status_code == 404
    assert r.json()["error"] != "voice_not_found"
