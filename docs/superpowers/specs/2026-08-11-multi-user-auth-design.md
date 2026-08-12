# Multi-user auth, key management, and voice sharing

**Date:** 2026-08-11
**Status:** Approved, ready for planning

## Problem

The server today has one shared bearer token (`MIMIC_API_TOKEN`) that grants
everything: synthesize with any voice, register clones, delete clones. Voices
live in a flat `reference/<name>/` directory with no ownership metadata.

The owner wants to give friends access to the server without giving them access
to his own cloned voice. That requires an identity model where none exists.

## Goals

- An admin key that can mint, inspect, and revoke per-user API keys.
- Keys are tracked: identity and lifecycle, usage counters, a request log, and
  enforced quotas.
- Users can upload their own voices and share them — either publicly or with
  named individuals.
- The admin can see and share out any voice on the server.
- **Reference audio is never downloadable by anyone.** A share confers
  "synthesize with this voice", never possession of the recording.

## Non-goals

Web UI changes; self-service signup; billing; key rotation for non-root keys
(revoke and re-mint instead); per-request audio retention.

## Storage

SQLite at `$MIMIC_DATA_DIR/mimic.db`, inside the existing `mimic-data` volume.
Reference audio stays on disk; the DB is the ownership index and source of
truth. Schema changes are forward-only migrations keyed on a `schema_version`
table.

```
api_keys      id, label (unique), token_hash, token_prefix, role (admin|user),
              enabled, created_at, last_used_at, expires_at,
              can_upload, max_voices, daily_char_quota, managed_by_env, notes

voices        id, owner_key_id, name, visibility (private|public), created_at
              unique(owner_key_id, name)

voice_grants  voice_id, grantee_key_id, granted_by, created_at
              unique(voice_id, grantee_key_id)

usage_events  id, key_id, ts, endpoint, voice_id, chars, audio_seconds, status
              index(key_id, ts)
```

Daily quota usage is computed as a sum over `usage_events` for the current UTC
day using the `(key_id, ts)` index. No separate rollup table — at this scale the
query is trivial, and one fewer thing to keep consistent.

## Identity

### Token format

`mk_<32 random bytes, base64url>`. Generated with `secrets.token_urlsafe`.
Displayed exactly once, at mint time. Stored as SHA-256. The `mk_xxxxxxxx`
prefix is stored in the clear for display in listings and for indexed lookup
before the constant-time hash comparison.

### The root admin key

`MIMIC_API_TOKEN` remains, and becomes the root admin identity. At startup it is
seeded into `api_keys` as a row with `role=admin` and `managed_by_env=1`. If the
env var changes, the row's hash is refreshed on next boot. The API refuses to
revoke, disable, or delete any `managed_by_env` key — that is the recovery path
if a minted admin key is lost.

Existing single-token deployments therefore keep working unchanged across the
upgrade.

Additional admin-role keys can be minted; they are ordinary rows and are
revokable.

### Local dev mode

If `MIMIC_API_TOKEN` is unset, the server is loopback-only (existing behavior,
enforced by `_check_public_bind_auth`). Auth stays off and every request
resolves as an implicit local admin caller. The dev workflow is unchanged.

## Voices

### Naming

A qualified voice name is `owner/voice`, where `owner` is a key label. A bare
name resolves in this order:

1. A backend built-in voice (`default`, etc.) — built-ins always win.
2. A voice owned by the caller.
3. A unique match among voices visible to the caller (public or granted).
4. Otherwise `409` listing the qualified candidates.

So the owner's existing `mimic clone say jim` keeps working, and a friend says
`jim/piper` for a voice shared with them.

### On-disk layout

`reference/<owner-label>/<voice-name>/{audio.wav,text.txt}`

Key labels are unique, so the tree stays human-legible on the server. Renaming a
key label moves its directory.

### Access rules

| Actor | Own voices | Public voices | Granted voices | Others' private |
|---|---|---|---|---|
| user | synth, delete, set visibility, grant | synth | synth | invisible (404) |
| admin | — | — | — | synth, delete, grant, list |

Voices are `private` at creation. Visibility and grants are independent: a voice
may be public, or private with an explicit grant list, or both.

Others' private voices return `404`, not `403` — a user should not be able to
enumerate what exists by probing names.

### Reference audio is never served

No endpoint returns the contents of `audio.wav` or `text.txt`, for any caller,
including admin. This is a hard invariant with a dedicated test. It is the
reason the feature exists.

## Quotas

Defaults for a newly minted key, all overridable at mint and patchable later:

- `can_upload = true`
- `max_voices = 5`
- `daily_char_quota = 50000`
- `expires_at = null`

Enforcement is a pre-flight check on every synthesis endpoint: today's recorded
characters plus the current request's character count against
`daily_char_quota`. Over quota returns `429` with a structured JSON body
(`{"error": "quota_exceeded", "used": N, "limit": N, "resets_at": "..."}`).
`max_voices` is checked on register. Admin-role keys are exempt from both.

Usage is recorded after a successful synthesis, capturing characters and
generated audio seconds.

## API surface

### New

```
GET    /me                                    identity, role, quotas, usage today

PATCH  /clone/voices/{name}                   {visibility: private|public}
POST   /clone/voices/{name}/grants            {grantee: "<label>"}   owner or admin
DELETE /clone/voices/{name}/grants/{label}

POST   /admin/keys                            mint; returns the token ONCE
GET    /admin/keys                            label, prefix, role, state, last used, usage
PATCH  /admin/keys/{label}                    quotas, can_upload, enabled, expiry
DELETE /admin/keys/{label}                    revoke (soft); ?purge=true also deletes voices
GET    /admin/usage                           rollups; ?key= ?since= ?limit= for raw events
GET    /admin/voices                          every voice, with owner and grant list
```

Revocation is soft: `enabled=0`, voices and history retained. Destroying a
user's uploads requires the explicit `?purge=true`.

### Changed

- `GET /health` — drops `registered_voices` and `models_loaded`. Returns only
  `{status, backend, stt_enabled}`. Those details move behind auth. (Today they
  leak every voice name to anonymous callers.)
- `GET /clone/voices` — backward compatible. `voices` remains a list of name
  strings; a new parallel `detail` array carries `{name, owner, visibility,
  mine}`. Installed clients keep working.
- `POST /clone/register` — registers under the caller; checks `can_upload` and
  `max_voices`.
- `DELETE /clone/voices/{name}` — own voices only; admin may delete any.
- `POST /tts`, `POST /clone/tts`, `POST /v1/audio/speech` — unchanged request
  and response shapes; resolve voices through the permission-aware lookup and
  record usage.
- `POST /clone/oneshot` — available to any authenticated key (the caller
  supplies their own reference audio), counts against the character quota.

## Wyoming

The Wyoming protocol has no authentication, so the server needs an assigned
identity. New setting `MIMIC_WYOMING_KEY=<label>` resolves at startup to that
key; the Wyoming server synthesizes with exactly that key's permissions and its
usage is attributed to it. Unset falls back to root admin with a startup
warning. Port 10200 remains LAN-only regardless — this is defense in depth, not
a substitute for the network boundary.

## Code structure

`app.py` is 451 lines of flat route registration and this work would push it
past 800. Split as part of the change:

```
db.py         connection, schema, migrations
identity.py   Key model, token mint/hash, `current_caller` dependency -> Caller
voices.py     registry: ownership, resolution, visibility, grants, disk layout
usage.py      quota pre-flight + event recording
routes/       tts.py, clones.py, admin.py, openai.py
app.py        wiring only
```

`auth.py`'s `require_token` becomes `current_caller`, returning a `Caller`
object rather than `None`. Every route that needs identity takes it as a
dependency, which makes "who is asking" impossible to omit.

## Client CLI

```
mimic admin key create dave [--quota 50000] [--max-voices 5] [--no-upload] [--expires 90d]
mimic admin keys
mimic admin key revoke dave
mimic admin usage [--key dave] [--since 7d]
mimic admin voices
mimic share <voice> --to dave | --public | --private
mimic unshare <voice> --from dave
mimic whoami
```

Friends use the existing commands — `mimic record`, `mimic clone say`,
`mimic clones` — pointed at the proxy with their own token in
`~/.config/mimic/config.toml`.

## Migration

On first boot against an existing install, before the server accepts requests:

1. Create the DB and apply migrations.
2. Seed the root key from `MIMIC_API_TOKEN`.
3. Adopt every existing `reference/<name>/` directory as a private voice owned
   by root.
4. Move those directories under `reference/<root-label>/`.

Idempotent, and safe to re-run.

## Error handling

| Condition | Status |
|---|---|
| missing or malformed bearer token | 401 |
| unknown, disabled, or expired key | 401 |
| non-admin hitting `/admin/*` | 403 |
| upload with `can_upload=false` | 403 |
| voice not visible to caller | 404 |
| ambiguous bare voice name | 409 |
| `max_voices` reached | 409 |
| daily character quota exceeded | 429 |

## Testing

- **Authorization matrix** — anonymous, user, other-user, and admin callers
  against every endpoint and every voice visibility state, asserting exact
  status codes. This is the core of the suite.
- **Reference audio invariant** — no endpoint, for any caller including admin,
  returns reference audio bytes.
- **Resolution** — bare, qualified, ambiguous, built-in shadowing, and
  cross-owner name collisions.
- **Quotas** — under, at, and over the limit; `max_voices`; admin exemption.
- **Migration** — starting from a flat reference dir with existing voices;
  re-running is a no-op.
- **End-to-end against a live server** — mint a key, register a voice as that
  key, confirm it cannot see or synth a private voice belonging to another
  owner, grant it, confirm it now can, revoke the key, confirm 401.
