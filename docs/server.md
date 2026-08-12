# Server reference

The mimic-tts server is a thin FastAPI wrapper around Qwen3-TTS, packaged
as a Docker image (`ghcr.io/<owner>/mimic-tts`) and as a `mimic-server`
console entry on PyPI's `mimic-server` workspace member (server is not
distributed via PyPI — install it via Docker or from source).

## Configuration (env vars)

| Variable | Default (local) | Default (Docker, with `MIMIC_DATA_DIR=/data`) | Purpose |
|---|---|---|---|
| `MIMIC_HOST` | `127.0.0.1` | `0.0.0.0` | Bind host |
| `MIMIC_PORT` | `8000` | `8000` | Bind port |
| `MIMIC_REFERENCE_DIR` | `./reference` | `/data/reference` | Persisted clone reference audio + transcripts |
| `MIMIC_MODEL_CACHE` | (HF default) | `/data/models` | Sets `HF_HOME`; weights cache here |
| `MIMIC_UNLOAD_AFTER` | `0` | `0` | Seconds idle before models unload (`0` = keep loaded forever) |
| `MIMIC_API_TOKEN` | unset | unset | Optional bearer token; becomes the admin key (see [Multi-user access](#multi-user-access)) |
| `MIMIC_LOG_LEVEL` | `INFO` | `INFO` | Log level |
| `MIMIC_BACKEND` | `chatterbox` | `chatterbox` | TTS engine (currently only `chatterbox`) |
| `MIMIC_DB_PATH` | `./mimic.db` | `/data/mimic.db` | SQLite database holding keys, voice ownership, grants, and usage |
| `MIMIC_ROOT_LABEL` | `root` | `root` | Label for the admin/root key seeded from `MIMIC_API_TOKEN`; also the owner directory legacy voices get adopted under |
| `MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND` | `false` | `false` | Allow public bind without `MIMIC_API_TOKEN` (set when a reverse proxy / tailnet ACL handles auth upstream) |
| `MIMIC_WYOMING_ENABLED` | `false` | `false` | Start the Wyoming TCP server alongside FastAPI (shared model in VRAM) |
| `MIMIC_WYOMING_HOST` | `0.0.0.0` | `0.0.0.0` | Wyoming bind interface (inside the container — host firewall is the actual boundary) |
| `MIMIC_WYOMING_PORT` | `10200` | `10200` | Wyoming TCP port |
| `MIMIC_WYOMING_KEY` | unset (falls back to root) | unset (falls back to root) | Label of the key the Wyoming server synthesizes as, since the Wyoming protocol has no auth of its own |

## Endpoints

All endpoints accept and return form-encoded data unless noted. Audio
responses are `audio/wav`.

### `POST /tts` — built-in voices
Form fields: `text` (required), `language` (default `English`), `speaker`
(default `default`), `instruct` (ignored by Chatterbox).

Chatterbox ships one built-in voice named `default`. For any other voice,
register a clone.

### `POST /clone/register` — register a clone
Form fields: `name` (default `default`), `ref_text` (the transcript),
`ref_audio` (file, ~3+ seconds wav).

The reference is persisted to `MIMIC_REFERENCE_DIR/<name>/audio.wav` +
`text.txt`. Subsequent calls to `/clone/tts` reload it from disk if the
in-memory prompt was unloaded.

### `POST /clone/tts` — synthesize using a registered clone
Form fields: `text`, `language`, `name`.

### `POST /clone/oneshot` — clone + synthesize in one call
Form fields: `text`, `language`, `ref_audio` (file), `ref_text`.
Slower than register-then-call, but doesn't persist anything.

### `GET /voices`, `GET /clone/voices`, `GET /health`, `GET /me`
`/voices` and `/clone/voices` are JSON lists (`/clone/voices` only lists
voices visible to the caller — see [Multi-user access](#multi-user-access)).
`/health` is always unauthenticated and deliberately uninformative: it
returns only `{status, backend, stt_enabled}` and does **not** report
`registered_voices` or loaded models, so it can't be used to enumerate
voices anonymously. Use `GET /clone/voices` (authenticated) or `mimic
clones` to see what's actually registered. `/me` returns the caller's own
identity, role, quota, and today's usage.

### `POST /v1/audio/speech` — OpenAI-compatible
JSON body matching OpenAI's TTS API:

```json
{
  "model": "tts-1",           // ignored, single engine
  "input": "text to speak",
  "voice": "default",         // built-in name OR registered clone name
  "response_format": "wav",   // wav | flac | pcm  (mp3/opus/aac require an encoder we don't ship)
  "speed": 1.0                // ignored (Chatterbox has no native speed knob)
}
```

Returns raw audio bytes with the appropriate Content-Type. Designed to be a
drop-in for the [`sfortis/openai_tts`](https://github.com/sfortis/openai_tts)
Home Assistant integration and any other tool that speaks OpenAI's TTS API
(open-webui, LibreChat, etc.).

## GPU + memory

Chatterbox loads on `cuda` (auto-falls back to CPU). Takes a few GB VRAM.

By default the model stays loaded once warm (`MIMIC_UNLOAD_AFTER=0`) — best
for low-latency interactive use like Home Assistant voice. Set it to a
positive number of seconds if you'd rather free VRAM after idle (useful
when sharing the GPU with other workloads like a local Ollama). First call
takes ~10s for the model to load; subsequent calls are fast.

## Auth

`MIMIC_API_TOKEN=secret` flips on bearer auth for every endpoint except
`/health`. The check uses `secrets.compare_digest` (constant-time).

**Public-bind safety check**: if `MIMIC_HOST` is non-loopback (e.g. `0.0.0.0`)
and `MIMIC_API_TOKEN` is unset, the server refuses to start. This prevents
the "oops I exposed it" scenario. If a reverse proxy / tailnet ACL is
enforcing auth upstream and you really do want no app-level token, set
`MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND=1` explicitly.

## Multi-user access

The server supports more than one caller identity behind a single
deployment: an admin mints a bearer key per person, each key owns its own
voices, and voices can be shared narrowly (one named person) or broadly
(anyone with a key) without ever handing out the underlying recording.

### Key lifecycle

`MIMIC_API_TOKEN` seeds the **root/admin key** (labeled `MIMIC_ROOT_LABEL`,
default `root`) at every boot — see [Upgrading from
single-token](#upgrading-from-single-token). From there, an admin manages
everyone else's keys over HTTP:

```
POST   /admin/keys              mint a key; the token is returned ONCE, never again
GET    /admin/keys              list keys: label, token prefix, role, state, usage
PATCH  /admin/keys/{label}      adjust quotas, can_upload, enabled, role, expiry
DELETE /admin/keys/{label}      revoke (soft: enabled=false, voices kept)
DELETE /admin/keys/{label}?purge=true   revoke AND delete the key's voices/files
                                 (the root/admin key can never be revoked or
                                 purged this way, with or without ?purge= --
                                 it is the recovery path if every other admin
                                 key is lost, and every request for it is a 403)
GET    /admin/usage             usage rollups; ?key=, ?since=, ?limit= for raw events
GET    /admin/voices            every voice on the server, with owner and grants
GET    /me                      the caller's own identity, role, quota, usage today
```

Every minted key gets, unless overridden at mint time: `can_upload=true`,
`max_voices=5`, `daily_char_quota=50000`, no expiry. Quota is enforced
pre-flight on every synthesis call (character count) and on register
(voice count); admin-role keys are exempt from both.

The root key — the one seeded from `MIMIC_API_TOKEN` — is protected by an
allowlist rather than a list of forbidden fields: a `PATCH` may change only
`notes`, `max_voices`, `daily_char_quota`, and `can_upload`. Anything else,
including any future field, is refused with a 403. It cannot be revoked,
purged, disabled, demoted, or given an expiry. That is deliberate: it is the
recovery path if a minted admin key is lost, and every one of those
operations would otherwise lock the owner out of the very endpoints they
would need to fix it, with no in-band way back.

### Visibility and grants

Every voice is `private` at creation, owned by whoever registered it.
Its owner (or an admin) can:

- `PATCH /clone/voices/{name}` `{"visibility": "public"}` — anyone with a
  key can now synthesize with it.
- `POST /clone/voices/{name}/grants` `{"grantee": "<label>"}` — that one
  key, specifically, can now synthesize with it, regardless of visibility.
- `DELETE /clone/voices/{name}/grants/{label}` — revoke that grant.

An admin key can see, synthesize with, grant, and delete *any* voice on the
server, not just its own.

A voice that is neither public nor granted to you does not exist as far as
you can tell: every route resolves it to `404 voice_not_found`, never `403`
— a caller must not be able to distinguish "no such voice" from "a voice
you can't touch" by probing names.

### Name resolution: bare vs. qualified

A voice name in a request is either bare (`"warm"`) or qualified
(`"dave/warm"`). Resolution order for a bare name:

1. A backend built-in voice (e.g. `default`) always wins first.
2. A voice you own by that name.
3. Exactly one voice visible to you (public, or granted to you) by that
   name — if more than one candidate matches, the server returns `409
   ambiguous_voice` listing the qualified candidates instead of guessing.

So the owner's existing `mimic clone say jim` keeps working unchanged, and
a friend who's been granted access says `jim/piper` (the qualified form)
for a voice that isn't theirs.

**Exception: `POST /clone/tts` checks the clone registry *before* built-ins**
(it passes `prefer_clone=True` to the shared resolver). Every other
synthesis route — `/tts`, `/v1/audio/speech` — follows the built-in-first
order above. This matters if you ever register a clone with the same name
as a built-in voice (e.g. `default`): `/clone/tts` reaches your clone,
while `/tts` and `/v1/audio/speech` still reach the built-in. (This is also
why `POST /clone/register` rejects registering a clone literally named
after a built-in voice — see `reserved_name`, `409` — the ambiguity is
worth refusing at registration time rather than surprising a caller later.)

### Reference audio is never downloadable

No endpoint — including every `/admin/*` route — returns the bytes of a
voice's `audio.wav` or its `text.txt` transcript, to any caller, for any
reason. Sharing a voice grants "synthesize with it," never "receive the
recording." This is treated as a hard invariant with a dedicated
regression test (`test_reference_audio_is_never_downloadable`).

### Wyoming and multi-user

The Wyoming protocol has no per-request authentication, so it runs as one
fixed identity: `MIMIC_WYOMING_KEY=<label>` if set, otherwise the
root/admin key (logged at `info` level as the expected default, not a
warning). A `MIMIC_WYOMING_KEY` that names a key which doesn't actually
exist **does** log a warning and falls back to root. All Wyoming requests
are synthesized and quota-tracked under that one key's permissions.

That identity is resolved **once, at startup**. Revoking, disabling, or
expiring the key named by `MIMIC_WYOMING_KEY` therefore has no effect on
Wyoming until the server restarts, and neither do quota changes to it.
Voice *grants* are checked per request and do take effect immediately — it
is the key's own state that is snapshotted. Restart the server after
revoking a key you had pointed Wyoming at.

## Upgrading from single-token

Deployments already running the single-shared-token version of this server
upgrade in place — no separate migration step to run by hand — but the
first boot on this version **moves your voice files on disk**, so back up
the data volume first. Stop the container before you do: tarring a live
SQLite file gives you a snapshot nobody promised to be consistent.

```bash
docker compose down

# Compose prefixes volume names with the project directory, so the volume
# declared as `mimic-data` is really `mimic-tts_mimic-data`. Confirm yours:
docker volume ls | grep mimic

docker run --rm -v mimic-tts_mimic-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/mimic-data-backup.tar.gz -C /data .

# Verify the backup is real before you trust it. A wrong volume name does
# NOT error — it creates a new empty volume and tars up nothing.
tar tzf mimic-data-backup.tar.gz | grep reference/
ls -lh mimic-data-backup.tar.gz
```

That verification step is not ceremony. If the volume name is wrong you get
a valid, tiny, empty tarball and no warning, and you will not find out until
you need it.

**Rolling back is not transparent.** After migration the reference audio
lives at `reference/<root-label>/<name>/`, and older images read only the
flat `reference/<name>/` layout — they will list zero clones. Downgrading
means restoring the backup, which is the whole reason to take one.

On first boot:

1. A SQLite database is created at `MIMIC_DB_PATH` (`/data/mimic.db` in
   Docker) inside the same data volume.
2. `MIMIC_API_TOKEN` is seeded as the admin key, labeled `MIMIC_ROOT_LABEL`
   (default `root`) — it keeps working exactly as before for every request
   you were already making.
3. Every voice that used to live flat at `reference/<name>/` is **adopted**
   by the root key and **moved** to `reference/<root-label>/<name>/`. Your
   existing `mimic clone say <name>` calls keep working unchanged, because
   bare names still resolve to voices you own first.

After the first boot, check `ls -a` on the reference directory. Two
special, dot-prefixed directories mean the migration preserved data it
couldn't place automatically and it needs a manual look — nothing was
deleted, but nothing was silently guessed either:

- `.migrate-staging` — voices that were mid-move when the process was
  interrupted; they finish installing on the next boot. If it's still
  there after a clean boot, something about its contents didn't validate
  (check the server log for the specific voice name).
- `.conflict-<name>` (or `.conflict-<name>-1`, `-2`, ...) — a legacy voice
  whose adopted destination already existed with *different* content
  (different `audio.wav` or `text.txt`). Both copies are kept; compare them
  and decide by hand which one to keep as `reference/<root-label>/<name>/`.

There is a third kind of leftover, and it is not dot-prefixed: a plain
directory still sitting at `reference/<name>/` after a clean boot. That
means it had a `text.txt` but no `audio.wav`, so it was not recognized as a
voice and was left untouched. The server logs a warning naming it. Either
supply the missing `audio.wav` and reboot, or re-record the voice.

**Do not change `MIMIC_ROOT_LABEL` after the first boot.** The root key is
looked up by label, so a new label mints a *second* admin key carrying the
same token hash, and which one authenticates is left to whichever row SQLite
returns first. Voices adopted under the old label stay there, owned by the
old key. If you want a different root label, set it before the first boot on
this version.

One visible API change: `GET /clone/voices` now returns **qualified** names
(`root/jim`, not `jim`). Bare names still resolve for their owner, so
`mimic clone say jim` keeps working — but anything parsing that list sees
the qualified form.

## Wyoming protocol (Home Assistant voice pipeline)

Opt-in via `MIMIC_WYOMING_ENABLED=true`. When enabled, a Wyoming TCP server
runs in the same process as FastAPI — both share the loaded model in VRAM,
no duplication.

**No auth**: the Wyoming protocol does not support auth, TLS, or any
handshake. The trust boundary is the network. The container binds to
`0.0.0.0:10200` by default; protect it by:

- Mapping the host port only to tailnet / LAN interfaces, or
- Not adding a public reverse-proxy entry for port 10200 (your existing
  HTTP reverse proxy won't accidentally pick this up — Wyoming is TCP, not
  HTTP).

Add to your `docker-compose.yml`:

```yaml
services:
  mimic-tts:
    ports:
      - "8000:8000"
      - "10200:10200"   # Wyoming — keep off the public internet
    environment:
      MIMIC_WYOMING_ENABLED: "true"
```

Then in Home Assistant, add the Wyoming integration pointing at
`tcp://<llmbox-host>:10200`. HA will discover the registered voices
(built-ins + clones) via the `Describe` event.

There's intentionally no token rotation, no per-user tokens, no JWT, and no
TLS termination — that's your reverse proxy's job. See
[self-hosting](self-hosting.md).
