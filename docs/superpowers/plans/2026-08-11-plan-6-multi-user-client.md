# Multi-User Auth — Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** `docs/superpowers/plans/2026-08-11-plan-5-multi-user-server.md` is complete and merged. Every endpoint this plan calls is built there.

**Goal:** Give the `mimic` CLI the commands to mint and manage API keys, share voices, and see usage — and make ordinary commands behave sensibly under the new permission and quota rules.

**Architecture:** `Client`/`AsyncClient` gain JSON-body support and one method per new endpoint. The CLI gains a `mimic admin` sub-app for key management plus top-level `whoami`, `share`, and `unshare`. Nothing about the existing `say` / `record` / `clone say` flow changes for an ordinary user.

**Tech Stack:** Python 3.12, httpx, typer, pytest.

## Global Constraints

- Python `>=3.12,<3.14` for the client package. Ruff line-length 100, `target-version = py312`.
- All new modules start with `from __future__ import annotations`.
- `./lint.sh` before every commit. Never `--no-verify`.
- `Client` and `AsyncClient` must stay feature-identical — every method added to one is added to the other, exercised by tests in both `client/tests/test_client.py` and `client/tests/test_async_client.py`.
- **A token is printed exactly once, at mint.** No command ever reads a token back from the server, because the server cannot return one.
- Comments follow `CLAUDE.md`: expressive naming over commentary.

---

### Task 1: JSON request bodies and a quota error type

The new endpoints take JSON bodies, but `build_request_spec` only knows about form `data` and `files`. Also, a 429 currently collapses into the generic `MimicAPIError`, which loses the quota fields.

**Files:**
- Modify: `client/mimic/_base.py`
- Modify: `client/mimic/errors.py`
- Modify: `client/mimic/client.py`
- Modify: `client/mimic/async_client.py`
- Test: `client/tests/test_base.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RequestSpec` gains `json: dict | None`; `build_request_spec(..., json=None)`; `MimicQuotaError(MimicAPIError)` with `.used`, `.limit`, `.resets_at`; `MimicForbiddenError(MimicAPIError)`.

- [ ] **Step 1: Write the failing tests**

```python
# client/tests/test_base.py — additions
import httpx
import pytest
from mimic._base import build_request_spec, raise_for_response
from mimic.errors import MimicForbiddenError, MimicQuotaError


def test_spec_carries_a_json_body():
    spec = build_request_spec(
        base_url="http://x", method="POST", path="/admin/keys", token="t",
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
    with pytest.raises(Exception) as exc:
        raise_for_response(response)
    assert "no voice 'x'" in str(exc.value)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest client/tests/test_base.py -v`
Expected: FAIL — `TypeError: build_request_spec() got an unexpected keyword argument 'json'`.

- [ ] **Step 3: Implement**

In `errors.py`:

```python
class MimicForbiddenError(MimicAPIError):
    """The key authenticated but is not allowed to do this."""


class MimicQuotaError(MimicAPIError):
    """The key's daily character quota is exhausted."""

    def __init__(self, status: int, detail: str, *, used: int = 0, limit: int = 0, resets_at: str = "") -> None:
        super().__init__(status, detail)
        self.used = used
        self.limit = limit
        self.resets_at = resets_at
```

In `_base.py`, add `json: dict[str, Any] | None = None` to `RequestSpec` and to `build_request_spec`, and extend `raise_for_response`:

```python
def _body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def raise_for_response(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    body = _body(response)
    detail = str(body.get("detail") or response.text or response.reason_phrase or "")
    if response.status_code == 401:
        raise MimicAuthError(response.status_code, detail)
    if response.status_code == 403:
        raise MimicForbiddenError(response.status_code, detail)
    if response.status_code == 404:
        raise MimicNotFoundError(response.status_code, detail)
    if response.status_code == 429:
        raise MimicQuotaError(
            response.status_code,
            detail,
            used=int(body.get("used", 0)),
            limit=int(body.get("limit", 0)),
            resets_at=str(body.get("resets_at", "")),
        )
    if 400 <= response.status_code < 500:
        raise MimicValidationError(response.status_code, detail)
    raise MimicAPIError(response.status_code, detail)
```

`_extract_detail` is now redundant — fold it into `_body` and delete it.

In both clients, thread `json` through `_request_json` into `self._http.request(..., json=spec.json)`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest client/tests -v`
Expected: all pass, including the pre-existing tests.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add client/mimic client/tests/test_base.py
git commit -m "feat(client): support JSON request bodies and typed quota errors"
```

---

### Task 2: Client methods for identity, sharing, and administration

**Files:**
- Modify: `client/mimic/client.py`
- Modify: `client/mimic/async_client.py`
- Test: `client/tests/test_client.py`
- Test: `client/tests/test_async_client.py`

**Interfaces:**
- Consumes: `build_request_spec(..., json=...)` from Task 1.
- Produces, on both `Client` and `AsyncClient`:
  - `whoami() -> dict[str, Any]` → `GET /me`
  - `list_clones() -> list[str]` (unchanged) and new `list_clone_detail() -> list[dict[str, Any]]` → the `detail` array from `GET /clone/voices`
  - `set_visibility(spec: str, visibility: str) -> dict[str, Any]` → `PATCH /clone/voices/{spec}`
  - `grant_voice(spec: str, grantee: str) -> dict[str, Any]` → `POST /clone/voices/{spec}/grants`
  - `revoke_voice_grant(spec: str, grantee: str) -> dict[str, Any]` → `DELETE /clone/voices/{spec}/grants/{grantee}`
  - `create_key(label: str, **fields) -> dict[str, Any]` → `POST /admin/keys`
  - `list_keys() -> list[dict[str, Any]]` → `GET /admin/keys`
  - `update_key(label: str, **fields) -> dict[str, Any]` → `PATCH /admin/keys/{label}`
  - `revoke_key(label: str, *, purge: bool = False) -> dict[str, Any]` → `DELETE /admin/keys/{label}`
  - `admin_usage(key: str | None = None, since: str | None = None, limit: int = 100) -> dict[str, Any]` → `GET /admin/usage`
  - `admin_voices() -> list[dict[str, Any]]` → `GET /admin/voices`

- [ ] **Step 1: Write the failing tests**

Follow the transport-mocking style already used in `client/tests/test_client.py`. One test per method, asserting both the request the client makes and the value it returns:

```python
def test_whoami_hits_me(monkeypatch):
    client, calls = _recording_client({"label": "dave", "role": "user"})
    assert client.whoami()["label"] == "dave"
    assert calls[-1]["method"] == "GET"
    assert calls[-1]["url"].endswith("/me")


def test_create_key_posts_json_and_returns_the_token(monkeypatch):
    client, calls = _recording_client({"label": "dave", "token": "mk_abc"})
    result = client.create_key("dave", max_voices=2, daily_char_quota=100)
    assert result["token"] == "mk_abc"
    assert calls[-1]["json"] == {"label": "dave", "max_voices": 2, "daily_char_quota": 100}


def test_create_key_omits_unset_fields(monkeypatch):
    client, calls = _recording_client({"label": "dave", "token": "mk_abc"})
    client.create_key("dave")
    assert calls[-1]["json"] == {"label": "dave"}


def test_grant_voice_targets_the_qualified_path(monkeypatch):
    client, calls = _recording_client({"status": "ok"})
    client.grant_voice("jim/piper", "dave")
    assert calls[-1]["url"].endswith("/clone/voices/jim/piper/grants")
    assert calls[-1]["json"] == {"grantee": "dave"}


def test_revoke_key_passes_purge(monkeypatch):
    client, calls = _recording_client({"status": "ok"})
    client.revoke_key("dave", purge=True)
    assert calls[-1]["method"] == "DELETE"
    assert "purge=true" in calls[-1]["url"]


def test_set_visibility_patches(monkeypatch):
    client, calls = _recording_client({"status": "ok", "visibility": "public"})
    assert client.set_visibility("warm", "public")["visibility"] == "public"
    assert calls[-1]["method"] == "PATCH"


def test_list_clone_detail_returns_the_detail_array(monkeypatch):
    payload = {
        "voices": ["dave/warm"],
        "detail": [{"name": "warm", "qualified": "dave/warm", "owner": "dave",
                    "visibility": "private", "mine": True}],
    }
    client, _ = _recording_client(payload)
    assert client.list_clone_detail()[0]["owner"] == "dave"


def test_list_clone_detail_tolerates_an_older_server(monkeypatch):
    """A server predating `detail` still answers list_clones(); detail is empty."""
    client, _ = _recording_client({"voices": ["warm"]})
    assert client.list_clone_detail() == []
```

Write `_recording_client(payload)` as a helper returning a `Client` whose transport records `{method, url, json, data}` and replies with `payload`. Mirror every test in `test_async_client.py`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest client/tests/test_client.py -v`
Expected: FAIL — `AttributeError: 'Client' object has no attribute 'whoami'`.

- [ ] **Step 3: Implement**

`create_key` and `update_key` build their JSON body from only the fields the caller passed, so server-side defaults stay authoritative:

```python
    def create_key(self, label: str, **fields: Any) -> dict[str, Any]:
        body = {"label": label, **{k: v for k, v in fields.items() if v is not None}}
        return self._request_json("POST", "/admin/keys", json=body)

    def update_key(self, label: str, **fields: Any) -> dict[str, Any]:
        body = {k: v for k, v in fields.items() if v is not None}
        return self._request_json("PATCH", f"/admin/keys/{label}", json=body)

    def revoke_key(self, label: str, *, purge: bool = False) -> dict[str, Any]:
        suffix = "?purge=true" if purge else ""
        return self._request_json("DELETE", f"/admin/keys/{label}{suffix}")

    def list_clone_detail(self) -> list[dict[str, Any]]:
        return self._request_json("GET", "/clone/voices").get("detail", [])
```

The rest are one-liners in the same shape. Duplicate all of it into `AsyncClient` with `await`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest client/tests -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add client/mimic client/tests
git commit -m "feat(client): add identity, sharing, and admin API methods"
```

---

### Task 3: `mimic whoami`, richer `mimic clones`, and quota-aware errors

**Files:**
- Modify: `client/mimic/cli.py`
- Test: `client/tests/test_cli.py`

**Interfaces:**
- Consumes: `Client.whoami`, `Client.list_clone_detail`, `MimicQuotaError`, `MimicForbiddenError`.
- Produces: `mimic whoami`; `mimic clones [--mine]`; a shared `_run` error-presentation wrapper.

- [ ] **Step 1: Write the failing tests**

Use typer's `CliRunner`, matching the existing style in `client/tests/test_cli.py`.

```python
def test_whoami_prints_identity_and_quota(monkeypatch):
    _stub_client(monkeypatch, whoami={
        "label": "dave", "role": "user", "can_upload": True,
        "max_voices": 5, "voices_used": 2, "daily_char_quota": 50000,
        "usage_today": {"requests": 3, "chars": 1200, "audio_seconds": 41.5},
    })
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 0
    assert "dave" in result.stdout
    assert "1,200 / 50,000" in result.stdout
    assert "2 / 5" in result.stdout


def test_whoami_shows_unlimited_for_zero_quota(monkeypatch):
    _stub_client(monkeypatch, whoami={
        "label": "root", "role": "admin", "can_upload": True,
        "max_voices": 5, "voices_used": 0, "daily_char_quota": 0,
        "usage_today": {"requests": 0, "chars": 0, "audio_seconds": 0.0},
    })
    assert "unlimited" in runner.invoke(app, ["whoami"]).stdout


def test_clones_shows_owner_and_visibility(monkeypatch):
    _stub_client(monkeypatch, clone_detail=[
        {"name": "warm", "qualified": "dave/warm", "owner": "dave", "visibility": "private", "mine": True},
        {"name": "piper", "qualified": "jim/piper", "owner": "jim", "visibility": "public", "mine": False},
    ])
    out = runner.invoke(app, ["clones"]).stdout
    assert "dave/warm" in out and "private" in out
    assert "jim/piper" in out and "public" in out


def test_clones_mine_filters_to_owned(monkeypatch):
    _stub_client(monkeypatch, clone_detail=[
        {"name": "warm", "qualified": "dave/warm", "owner": "dave", "visibility": "private", "mine": True},
        {"name": "piper", "qualified": "jim/piper", "owner": "jim", "visibility": "public", "mine": False},
    ])
    out = runner.invoke(app, ["clones", "--mine"]).stdout
    assert "dave/warm" in out
    assert "jim/piper" not in out


def test_clones_falls_back_when_server_has_no_detail(monkeypatch):
    _stub_client(monkeypatch, clone_detail=[], clones=["warm"])
    assert "warm" in runner.invoke(app, ["clones"]).stdout


def test_quota_error_is_a_clean_message_not_a_traceback(monkeypatch):
    _stub_client(monkeypatch, say_raises=MimicQuotaError(
        429, "daily character quota exceeded (95/100)", used=95, limit=100,
        resets_at="2026-08-12T00:00:00+00:00",
    ))
    result = runner.invoke(app, ["say", "hello"])
    assert result.exit_code == 1
    assert "quota" in result.stdout.lower()
    assert "95 / 100" in result.stdout
    assert "Traceback" not in result.stdout


def test_forbidden_error_is_a_clean_message(monkeypatch):
    _stub_client(monkeypatch, say_raises=MimicForbiddenError(403, "admin key required"))
    result = runner.invoke(app, ["say", "hello"])
    assert result.exit_code == 1
    assert "admin key required" in result.stdout
    assert "Traceback" not in result.stdout
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest client/tests/test_cli.py -v`
Expected: FAIL — no `whoami` command.

- [ ] **Step 3: Implement**

Add the error wrapper first, and apply it to every command that talks to the server:

```python
def _run(action: Callable[[], T]) -> T:
    """Turn API errors into a one-line message and a non-zero exit.

    A traceback is the wrong output for 'your friend's key ran out of quota'.
    """
    try:
        return action()
    except MimicQuotaError as e:
        typer.echo(f"quota exceeded: {e.used:,} / {e.limit:,} characters today", err=True)
        if e.resets_at:
            typer.echo(f"resets at {e.resets_at}", err=True)
        raise typer.Exit(1) from e
    except MimicForbiddenError as e:
        typer.echo(f"not permitted: {e}", err=True)
        raise typer.Exit(1) from e
    except MimicAuthError as e:
        typer.echo(f"authentication failed: {e}", err=True)
        typer.echo("check `token` in ~/.config/mimic/config.toml", err=True)
        raise typer.Exit(1) from e
    except MimicNotFoundError as e:
        typer.echo(f"not found: {e}", err=True)
        raise typer.Exit(1) from e
```

Note for the implementer: `CliRunner` mixes stderr into `stdout` by default, which is why the tests assert against `result.stdout`. If the runner in this repo is constructed with `mix_stderr=False`, assert against `result.stderr` instead.

Then the two commands:

```python
@app.command()
def whoami() -> None:
    """Show which key you are, what you may do, and today's usage."""
    with _client() as c:
        me = _run(c.whoami)
    quota = me["daily_char_quota"]
    used = me["usage_today"]["chars"]
    budget = "unlimited" if quota == 0 else f"{used:,} / {quota:,}"
    typer.echo(f"key           {me['label']} ({me['role']})")
    typer.echo(f"upload        {'yes' if me['can_upload'] else 'no'}")
    typer.echo(f"voices        {me['voices_used']} / {me['max_voices']}")
    typer.echo(f"chars today   {budget}")
    typer.echo(f"requests      {me['usage_today']['requests']}")


@app.command()
def clones(mine: Annotated[bool, typer.Option(help="Only voices you own.")] = False) -> None:
    """List clone voices you can use."""
    with _client() as c:
        detail = _run(c.list_clone_detail)
        if not detail:
            for name in _run(c.list_clones):
                typer.echo(name)
            return
    for v in detail:
        if mine and not v["mine"]:
            continue
        marker = "*" if v["mine"] else " "
        typer.echo(f"{marker} {v['qualified']:32s} {v['visibility']}")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest client/tests/test_cli.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add client/mimic/cli.py client/tests/test_cli.py
git commit -m "feat(client): add whoami, richer clones listing, and readable API errors"
```

---

### Task 4: `mimic share` and `mimic unshare`

**Files:**
- Modify: `client/mimic/cli.py`
- Test: `client/tests/test_cli.py`

**Interfaces:**
- Consumes: `Client.grant_voice`, `Client.revoke_voice_grant`, `Client.set_visibility`.
- Produces: `mimic share <voice> [--to LABEL] [--public] [--private]`; `mimic unshare <voice> --from LABEL`.

- [ ] **Step 1: Write the failing tests**

```python
def test_share_to_a_person_grants(monkeypatch):
    stub = _stub_client(monkeypatch)
    result = runner.invoke(app, ["share", "warm", "--to", "dave"])
    assert result.exit_code == 0
    assert stub.calls == [("grant_voice", "warm", "dave")]
    assert "dave" in result.stdout


def test_share_public_sets_visibility(monkeypatch):
    stub = _stub_client(monkeypatch)
    assert runner.invoke(app, ["share", "warm", "--public"]).exit_code == 0
    assert stub.calls == [("set_visibility", "warm", "public")]


def test_share_private_unpublishes(monkeypatch):
    stub = _stub_client(monkeypatch)
    assert runner.invoke(app, ["share", "warm", "--private"]).exit_code == 0
    assert stub.calls == [("set_visibility", "warm", "private")]


def test_share_requires_exactly_one_target(monkeypatch):
    _stub_client(monkeypatch)
    bare = runner.invoke(app, ["share", "warm"])
    assert bare.exit_code == 2
    assert "--to" in bare.stdout

    both = runner.invoke(app, ["share", "warm", "--to", "dave", "--public"])
    assert both.exit_code == 2


def test_share_public_and_private_together_is_rejected(monkeypatch):
    _stub_client(monkeypatch)
    assert runner.invoke(app, ["share", "warm", "--public", "--private"]).exit_code == 2


def test_unshare_revokes(monkeypatch):
    stub = _stub_client(monkeypatch)
    assert runner.invoke(app, ["unshare", "warm", "--from", "dave"]).exit_code == 0
    assert stub.calls == [("revoke_voice_grant", "warm", "dave")]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest client/tests/test_cli.py -k share -v`
Expected: FAIL — no such command.

- [ ] **Step 3: Implement**

```python
@app.command()
def share(
    voice: Annotated[str, typer.Argument(help="Voice name, or owner/name.")],
    to: Annotated[str | None, typer.Option("--to", help="Grant to this key label.")] = None,
    public: Annotated[bool, typer.Option("--public", help="Let every key use it.")] = False,
    private: Annotated[bool, typer.Option("--private", help="Unpublish it.")] = False,
) -> None:
    """Share a voice with one person, or publish it to everyone."""
    chosen = [bool(to), public, private]
    if sum(chosen) != 1:
        typer.echo("pass exactly one of --to LABEL, --public, or --private", err=True)
        raise typer.Exit(2)
    with _client() as c:
        if to:
            _run(lambda: c.grant_voice(voice, to))
            typer.echo(f"shared {voice} with {to}")
        else:
            visibility = "public" if public else "private"
            _run(lambda: c.set_visibility(voice, visibility))
            typer.echo(f"{voice} is now {visibility}")


@app.command()
def unshare(
    voice: Annotated[str, typer.Argument(help="Voice name, or owner/name.")],
    from_: Annotated[str, typer.Option("--from", help="Key label to revoke.")],
) -> None:
    """Revoke one person's access to a voice."""
    with _client() as c:
        _run(lambda: c.revoke_voice_grant(voice, from_))
    typer.echo(f"revoked {from_}'s access to {voice}")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest client/tests/test_cli.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
./lint.sh
git add client/mimic/cli.py client/tests/test_cli.py
git commit -m "feat(client): add share and unshare commands"
```

---

### Task 5: `mimic admin` sub-app

**Files:**
- Create: `client/mimic/admin_cli.py`
- Modify: `client/mimic/cli.py` (mount the sub-app)
- Test: `client/tests/test_admin_cli.py`

**Interfaces:**
- Consumes: the admin methods from Task 2, `_client` and `_run` from `cli.py`.
- Produces: `mimic admin key create|revoke`, `mimic admin keys`, `mimic admin usage`, `mimic admin voices`.

`admin_cli.py` is a separate module so `cli.py` stays readable — it is already the largest file in the client package.

- [ ] **Step 1: Write the failing tests**

```python
def test_key_create_prints_the_token_with_a_warning(monkeypatch):
    stub = _stub_client(monkeypatch, create_key={"label": "dave", "token": "mk_secret123"})
    result = runner.invoke(app, ["admin", "key", "create", "dave"])
    assert result.exit_code == 0
    assert "mk_secret123" in result.stdout
    assert "shown once" in result.stdout.lower()
    assert stub.calls == [("create_key", "dave", {})]


def test_key_create_passes_only_the_options_given(monkeypatch):
    stub = _stub_client(monkeypatch, create_key={"label": "dave", "token": "mk_x"})
    runner.invoke(app, ["admin", "key", "create", "dave", "--quota", "100", "--no-upload"])
    assert stub.calls == [("create_key", "dave", {"daily_char_quota": 100, "can_upload": False})]


def test_key_create_admin_role(monkeypatch):
    stub = _stub_client(monkeypatch, create_key={"label": "co", "token": "mk_x"})
    runner.invoke(app, ["admin", "key", "create", "co", "--admin"])
    assert stub.calls == [("create_key", "co", {"role": "admin"})]


def test_keys_lists_a_table(monkeypatch):
    _stub_client(monkeypatch, list_keys=[
        {"label": "root", "token_prefix": "abcd1234", "role": "admin", "enabled": True,
         "last_used_at": "2026-08-11T10:00:00+00:00", "daily_char_quota": 0,
         "usage": {"requests": 4, "chars": 900, "audio_seconds": 30.0}},
        {"label": "dave", "token_prefix": "efgh5678", "role": "user", "enabled": False,
         "last_used_at": None, "daily_char_quota": 50000,
         "usage": {"requests": 0, "chars": 0, "audio_seconds": 0.0}},
    ])
    out = runner.invoke(app, ["admin", "keys"]).stdout
    assert "root" in out and "dave" in out
    assert "revoked" in out
    assert "mk_efgh5678" in out or "efgh5678" in out


def test_key_revoke_defaults_to_soft(monkeypatch):
    stub = _stub_client(monkeypatch, revoke_key={"status": "ok"})
    assert runner.invoke(app, ["admin", "key", "revoke", "dave"]).exit_code == 0
    assert stub.calls == [("revoke_key", "dave", False)]


def test_key_revoke_purge_requires_confirmation(monkeypatch):
    stub = _stub_client(monkeypatch, revoke_key={"status": "ok"})
    declined = runner.invoke(app, ["admin", "key", "revoke", "dave", "--purge"], input="n\n")
    assert stub.calls == []
    assert declined.exit_code != 0 or "aborted" in declined.stdout.lower()

    accepted = runner.invoke(app, ["admin", "key", "revoke", "dave", "--purge"], input="y\n")
    assert accepted.exit_code == 0
    assert stub.calls == [("revoke_key", "dave", True)]


def test_usage_prints_totals(monkeypatch):
    _stub_client(monkeypatch, admin_usage={
        "totals": [{"label": "dave", "requests": 3, "chars": 1200, "audio_seconds": 40.0}],
        "events": [],
    })
    out = runner.invoke(app, ["admin", "usage"]).stdout
    assert "dave" in out and "1,200" in out


def test_voices_shows_owner_visibility_and_grants(monkeypatch):
    _stub_client(monkeypatch, admin_voices=[
        {"qualified": "jim/piper", "owner": "jim", "visibility": "private",
         "created_at": "2026-08-01T00:00:00+00:00", "grants": ["dave", "erin"]},
    ])
    out = runner.invoke(app, ["admin", "voices"]).stdout
    assert "jim/piper" in out
    assert "dave, erin" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest client/tests/test_admin_cli.py -v`
Expected: FAIL — no `admin` command group.

- [ ] **Step 3: Implement**

```python
"""`mimic admin` — key minting, revocation, usage, and server-wide voice listing."""

from __future__ import annotations

import typer

admin_app = typer.Typer(no_args_is_help=True, help="Admin operations (requires an admin key)")
key_app = typer.Typer(no_args_is_help=True, help="API key management")
admin_app.add_typer(key_app, name="key")


@key_app.command("create")
def key_create(
    label: Annotated[str, typer.Argument(help="Short name for this key, e.g. 'dave'.")],
    quota: Annotated[int | None, typer.Option(help="Daily character limit; 0 = unlimited.")] = None,
    max_voices: Annotated[int | None, typer.Option(help="How many voices they may upload.")] = None,
    no_upload: Annotated[bool, typer.Option("--no-upload", help="Forbid uploading voices.")] = False,
    expires: Annotated[str | None, typer.Option(help="ISO-8601 expiry, e.g. 2027-01-01.")] = None,
    admin: Annotated[bool, typer.Option("--admin", help="Mint an admin key.")] = False,
    notes: Annotated[str | None, typer.Option(help="Free-text note.")] = None,
) -> None:
    """Mint a new API key. The token is printed once and cannot be recovered."""
    fields: dict[str, object] = {}
    if quota is not None:
        fields["daily_char_quota"] = quota
    if max_voices is not None:
        fields["max_voices"] = max_voices
    if no_upload:
        fields["can_upload"] = False
    if expires is not None:
        fields["expires_at"] = _parse_expiry(expires)
    if admin:
        fields["role"] = "admin"
    if notes is not None:
        fields["notes"] = notes

    with _client() as c:
        created = _run(lambda: c.create_key(label, **fields))

    typer.echo(f"key '{created['label']}' created\n")
    typer.echo(f"  {created['token']}\n")
    typer.secho(
        "This token is shown once. Copy it now — the server stores only a hash.",
        fg=typer.colors.YELLOW,
    )
```

`_parse_expiry` accepts either a bare date (`2027-01-01` → midnight UTC) or a duration (`90d`, `12h`) and returns an ISO-8601 UTC string. Put it in `admin_cli.py` with its own unit tests:

```python
def test_parse_expiry_accepts_a_date():
    assert _parse_expiry("2027-01-01").startswith("2027-01-01T00:00:00")


def test_parse_expiry_accepts_a_duration(monkeypatch):
    result = _parse_expiry("90d")
    assert result > _parse_expiry("1d")


def test_parse_expiry_rejects_garbage():
    with pytest.raises(typer.BadParameter):
        _parse_expiry("soonish")
```

`keys` renders a fixed-width table with columns `LABEL, PREFIX, ROLE, STATE, LAST USED, CHARS TODAY`, where `STATE` is `active` or `revoked`, `LAST USED` is `never` when null, and a zero quota renders as `unlimited`.

`key revoke` takes `--purge`, and when purging calls `typer.confirm(f"Permanently delete {label} and every voice they uploaded?", abort=True)` before acting.

`usage` takes `--key`, `--since` (same duration/date parsing), and `--events` to also print the raw request log. `voices` renders `QUALIFIED, VISIBILITY, SHARED WITH` with grants joined by `", "` and `—` when empty.

Mount it in `cli.py`: `app.add_typer(admin_app, name="admin")`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest client/tests -v`
Expected: all pass.

- [ ] **Step 5: Update the docs**

- `client/README.md` and `docs/client.md` — an "Admin and sharing" section covering `whoami`, `share`/`unshare`, and the `mimic admin` group, with a worked example: mint a key for a friend, tell them what to put in their `config.toml`, share one voice with them, check their usage, revoke.
- `README.md` — add the multi-user commands to the quick-start block.
- Note explicitly in both that a friend's `config.toml` needs `server_url` and their own `token`, and that they should use qualified names (`jim/piper`) for voices shared with them.

- [ ] **Step 6: Verify against the real server**

With the Plan 5 server running locally (`MIMIC_API_TOKEN=dev-admin`), against a scratch config dir so your real `~/.config/mimic/config.toml` is untouched:

```bash
export MIMIC_CONFIG_DIR=/tmp/mimic-admin-check
mkdir -p "$MIMIC_CONFIG_DIR"
printf 'server_url = "http://localhost:8000"\ntoken = "dev-admin"\n' > "$MIMIC_CONFIG_DIR/config.toml"

mimic whoami                                   # admin
mimic admin key create testfriend --quota 100  # copy the token
mimic admin keys                               # testfriend listed, active
mimic clones                                   # your voices, marked with *

# now as the friend
printf 'server_url = "http://localhost:8000"\ntoken = "<their token>"\n' > "$MIMIC_CONFIG_DIR/config.toml"
mimic whoami                                   # role user, 0 / 100 chars
mimic clones                                   # empty — cannot see your private voices
mimic clone say <your-voice> "should fail"     # not found

# back as admin: share, then re-check as the friend
mimic share <your-voice> --to testfriend
mimic clone say <owner>/<your-voice> "should work now"

mimic admin key revoke testfriend --purge
```

Step "cannot see your private voices" followed by "works after the share" is the acceptance criterion for the whole feature.

- [ ] **Step 7: Commit**

```bash
./lint.sh
git add -A client docs README.md
git commit -m "feat(client): add mimic admin command group and docs"
```

---

## Self-review notes

Spec coverage for the client half: the CLI block in the spec lists `admin key create`, `admin keys`, `admin key revoke`, `admin usage`, `admin voices`, `share`, `unshare`, and `whoami` — Tasks 3, 4, and 5 cover all eight. Tasks 1 and 2 exist because the spec's endpoints need JSON bodies and typed errors the client doesn't have yet.

Method names are consistent across tasks: `grant_voice` / `revoke_voice_grant` / `set_visibility` / `create_key` / `list_keys` / `update_key` / `revoke_key` / `admin_usage` / `admin_voices` / `whoami` / `list_clone_detail`, defined in Task 2 and used unchanged in Tasks 3-5.

**Deliberate omission:** no `mimic admin key rotate`. The spec lists key rotation as a non-goal — revoke and re-mint.
