"""The consolidated regression net for the whole multi-user feature.

One parametrized test drives every registered route against four actors —
anonymous, the owner of a private voice, an unrelated other user, and an
admin — and checks the exact status code each is entitled to. `CASES` is
compared against `app.routes` in `test_matrix_covers_every_route` below so a
newly added route can't silently ship without an authorization case.

Every case runs against a freshly built app (`matrix_env`, a factory
fixture) so destructive verbs (DELETE, PATCH) never leak state from one
actor's turn into the next actor's expectation.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import _wav
from mimic_server.app import build_app
from mimic_server.config import Settings
from starlette.routing import Mount

ANONYMOUS, OWNER, OTHER, ADMIN = "anonymous", "dave", "erin", "root"

# (method, path, body-kind, {actor: expected_status})
#
# `dave` owns a private voice "warm" (seeded by `matrix_env`); `erin` has no
# relationship to it and must be turned away with 404 -- never 403, which
# would confirm the voice exists to a caller who can't see it.
CASES: list[tuple[str, str, str | None, dict[str, int]]] = [
    ("GET", "/health", None, {ANONYMOUS: 200, OWNER: 200, OTHER: 200, ADMIN: 200}),
    ("GET", "/me", None, {ANONYMOUS: 401, OWNER: 200, OTHER: 200, ADMIN: 200}),
    ("GET", "/voices", None, {ANONYMOUS: 401, OWNER: 200, OTHER: 200, ADMIN: 200}),
    ("POST", "/stt", "stt", {ANONYMOUS: 401, OWNER: 503, OTHER: 503, ADMIN: 503}),
    ("POST", "/tts", "tts_default", {ANONYMOUS: 401, OWNER: 200, OTHER: 200, ADMIN: 200}),
    ("GET", "/clone/voices", None, {ANONYMOUS: 401, OWNER: 200, OTHER: 200, ADMIN: 200}),
    (
        "POST",
        "/clone/register",
        "register",
        {ANONYMOUS: 401, OWNER: 200, OTHER: 200, ADMIN: 200},
    ),
    # dave owns a private voice "warm"; erin must not reach it by any route.
    (
        "POST",
        "/clone/voices/dave/warm/grants",
        "grant",
        {ANONYMOUS: 401, OWNER: 200, OTHER: 404, ADMIN: 200},
    ),
    (
        "DELETE",
        "/clone/voices/dave/warm/grants/erin",
        None,
        {ANONYMOUS: 401, OWNER: 200, OTHER: 404, ADMIN: 200},
    ),
    (
        "PATCH",
        "/clone/voices/dave/warm",
        "publish",
        {ANONYMOUS: 401, OWNER: 200, OTHER: 404, ADMIN: 200},
    ),
    (
        "DELETE",
        "/clone/voices/dave/warm",
        None,
        {ANONYMOUS: 401, OWNER: 200, OTHER: 404, ADMIN: 200},
    ),
    (
        "POST",
        "/clone/tts",
        "synth_warm",
        {ANONYMOUS: 401, OWNER: 200, OTHER: 404, ADMIN: 200},
    ),
    ("POST", "/clone/oneshot", "oneshot", {ANONYMOUS: 401, OWNER: 200, OTHER: 200, ADMIN: 200}),
    (
        "POST",
        "/v1/audio/speech",
        "openai_speech",
        {ANONYMOUS: 401, OWNER: 200, OTHER: 200, ADMIN: 200},
    ),
    ("GET", "/admin/keys", None, {ANONYMOUS: 401, OWNER: 403, OTHER: 403, ADMIN: 200}),
    ("POST", "/admin/keys", "mint", {ANONYMOUS: 401, OWNER: 403, OTHER: 403, ADMIN: 200}),
    (
        "PATCH",
        "/admin/keys/dave",
        "patch_key",
        {ANONYMOUS: 401, OWNER: 403, OTHER: 403, ADMIN: 200},
    ),
    (
        "DELETE",
        "/admin/keys/erin",
        None,
        {ANONYMOUS: 401, OWNER: 403, OTHER: 403, ADMIN: 200},
    ),
    ("GET", "/admin/usage", None, {ANONYMOUS: 401, OWNER: 403, OTHER: 403, ADMIN: 200}),
    ("GET", "/admin/voices", None, {ANONYMOUS: 401, OWNER: 403, OTHER: 403, ADMIN: 200}),
]


# Each builder takes the acting actor's label (unused by most) and returns
# the `client.request(**kwargs)` payload for that case. A dict dispatch
# keeps this at one branch per body kind instead of a long if/elif chain.
_BODY_BUILDERS: dict[str, Any] = {
    "mint": lambda actor: {"json": {"label": f"minted-by-{actor}"}},
    "synth_warm": lambda _actor: {"data": {"text": "hi", "name": "dave/warm"}},
    "publish": lambda _actor: {"json": {"visibility": "public"}},
    "grant": lambda _actor: {"json": {"grantee": "erin"}},
    "tts_default": lambda _actor: {"data": {"text": "hi", "speaker": "default"}},
    "stt": lambda _actor: {"files": {"audio": ("clip.wav", _wav(), "audio/wav")}},
    "register": lambda actor: {
        "data": {"name": f"brandnew-{actor}", "ref_text": "hello"},
        "files": {"ref_audio": ("a.wav", _wav(), "audio/wav")},
    },
    "oneshot": lambda _actor: {
        "data": {"text": "hi", "ref_text": "hello"},
        "files": {"ref_audio": ("a.wav", _wav(), "audio/wav")},
    },
    "openai_speech": lambda _actor: {"json": {"input": "hi", "voice": "default"}},
    "patch_key": lambda actor: {"json": {"notes": f"note-by-{actor}"}},
}


def _body_for(kind: str | None, actor: str) -> dict[str, Any]:
    if kind is None:
        return {}
    try:
        builder = _BODY_BUILDERS[kind]
    except KeyError:
        raise ValueError(f"unknown body kind: {kind!r}") from None
    return builder(actor)


@pytest.mark.parametrize(("method", "path", "body_kind", "expected"), CASES)
@pytest.mark.parametrize("actor", [ANONYMOUS, OWNER, OTHER, ADMIN])
def test_authorization_matrix(matrix_env, method, path, body_kind, expected, actor):
    """Each case runs against a freshly seeded server so destructive verbs
    (DELETE, PATCH) can't leak state into the next actor's expectation."""
    client, tokens = matrix_env()
    headers = {} if actor == ANONYMOUS else {"Authorization": f"Bearer {tokens[actor]}"}
    kwargs = _body_for(body_kind, actor)
    response = client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == expected[actor], response.text


def test_reference_audio_is_never_downloadable(matrix_env):
    """No route -- not even an admin one -- ever returns raw reference audio
    bytes. `dave/warm`'s reference wav is silence, not RIFF-distinctive
    audio, so this also guards against a route that returns *some* wav
    (e.g. a synthesis result) being mistaken for the stored reference."""
    client, tokens = matrix_env()
    admin = {"Authorization": f"Bearer {tokens['root']}"}
    probes = [
        "/clone/voices/dave/warm/audio.wav",
        "/clone/voices/dave/warm/text.txt",
        "/reference/dave/warm/audio.wav",
        "/reference/dave/warm/text.txt",
    ]
    for path in probes:
        r = client.get(path, headers=admin)
        assert r.status_code in (404, 405), path
        assert b"RIFF" not in r.content


# CASES exercises concrete voice/key names ("dave/warm", "erin") rather than
# the route's path template ("{spec:path}", "{label}"). This maps each
# concrete case path to the template it actually hits, so coverage can be
# checked against `app.routes` without the two ever being the same string.
_TEMPLATE_FOR_CASE_PATH: dict[str, str] = {
    "/clone/voices/dave/warm/grants": "/clone/voices/{spec:path}/grants",
    "/clone/voices/dave/warm/grants/erin": "/clone/voices/{spec:path}/grants/{grantee}",
    "/clone/voices/dave/warm": "/clone/voices/{spec:path}",
    "/admin/keys/dave": "/admin/keys/{label}",
    "/admin/keys/erin": "/admin/keys/{label}",
}


# The web UI, when MIMIC_WEB_DIST is set, is mounted as a Starlette `Mount`
# rather than a route with `.methods` -- `getattr(route, "methods", ())`
# below would silently skip it and any future mount, which would slip
# straight past a check whose whole point is "no endpoint ships unaudited."
_ALLOWED_MOUNT_NAMES = frozenset({"web"})


def test_matrix_covers_every_route(matrix_env):
    """Fail loudly if a route gets added/removed without updating `CASES` --
    the whole point of this file is that no endpoint ships unaudited."""
    client, _tokens = matrix_env()
    app = client.app

    mounts = [route for route in app.routes if isinstance(route, Mount)]
    unexpected = [m for m in mounts if m.name not in _ALLOWED_MOUNT_NAMES]
    assert not unexpected, (
        f"unaudited mount(s) not covered by the authorization matrix: "
        f"{[(m.name, m.path) for m in unexpected]}"
    )

    registered = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", ())
        if method != "HEAD"
    }
    covered = {
        (method, _TEMPLATE_FOR_CASE_PATH.get(path, path))
        for method, path, _kind, _expected in CASES
    }
    assert covered == registered


def test_web_ui_mount_is_allowlisted_not_unaudited(tmp_path, fake_backend):
    """A real MIMIC_WEB_DIST mount must not trip the "unaudited mount"
    guard -- it's the one Mount this app is expected to register."""
    settings = Settings(
        reference_dir=tmp_path / "reference",
        db_path=tmp_path / "mimic.db",
        api_token="root-token",  # noqa: S106
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MIMIC_WEB_DIST", str(tmp_path))
        app = build_app(settings, backend_factory=lambda _s: fake_backend)
    mounts = [route for route in app.routes if isinstance(route, Mount)]
    assert any(m.name == "web" for m in mounts)
    assert all(m.name in _ALLOWED_MOUNT_NAMES for m in mounts)


def test_unexpected_mount_name_is_not_allowlisted():
    """Direct check on the guard's allowlist: a differently-named mount
    (e.g. something added later that serves files from disk) is exactly
    what `test_matrix_covers_every_route` is meant to catch."""
    rogue = Mount("/rogue", app=lambda *_args: None, name="rogue-static-dump")
    assert rogue.name not in _ALLOWED_MOUNT_NAMES
