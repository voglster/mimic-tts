# Client reference

`pip install mimic-tts` gives you a `mimic` CLI and an importable
`mimic` Python library (sync + async).

## Configuration

The client resolves config in this order, first match wins:

1. Constructor kwargs / CLI flags
2. Env vars: `MIMIC_SERVER_URL`, `MIMIC_API_TOKEN`
3. `~/.config/mimic/config.toml` (cross-platform via platformdirs)
4. Defaults (`http://localhost:8000`, no token, default voice `default`)

Example `config.toml`:

```toml
server_url = "http://nas.local:8000"
token = "optional"
default_voice = "default"
```

Override the config dir for tests with `MIMIC_CONFIG_DIR`.

## CLI reference

```
mimic say <text> [--voice NAME] [--out FILE] [--language English]
mimic record <name>                              # guided recording flow
mimic record <name> --audio FILE --text "..."   # skip the recorder
mimic clone say <name> <text> [--out FILE] [--language English]
mimic voices                                     # list built-in voices
mimic clones                                     # list registered clones
mimic config                                     # print effective config
mimic health
mimic whoami                                     # your key, role, quota, usage today
mimic share <voice> --to <label>                 # grant one person access to your voice
mimic share <voice> --public                     # publish it to every key
mimic share <voice> --private                    # unpublish it
mimic unshare <voice> --from <label>              # revoke one person's access
mimic admin key create <label> [--quota N] [--max-voices N] [--no-upload]
                                [--expires DATE|DURATION] [--admin] [--notes TEXT]
mimic admin key revoke <label> [--purge]         # --purge deletes their uploads too
mimic admin keys                                 # every key: role, state, last used, usage
mimic admin usage [--key LABEL] [--since DATE|DURATION] [--events]
mimic admin voices                               # every voice: owner, visibility, grants
```

The interactive `mimic record <name>` flow:

1. Prints a 4-sentence script chosen for varied phonemes.
2. "Press Enter to start recording, Ctrl+C to abort."
3. Records from the default mic until you press Enter again (cap 30s).
4. Plays back the take.
5. "Keep this take? [y/N/r=retry]" — `r` re-records, `y` keeps.
6. Asks for the transcript (defaulting to the printed script).
7. POSTs to `/clone/register`.

## Admin and sharing

If the server has `MIMIC_API_TOKEN` set, every key is scoped: a key owns the
voices it registers, and can see only its own private voices plus anything
public or explicitly shared with it. An **admin** key additionally gets the
`mimic admin` command group for minting and managing other keys.

### Worked example: bringing a friend onto your server

You (`jim`, an admin key) want to give your friend Dave access to your
server and let him use one of your cloned voices.

**1. Mint Dave a key.**

```bash
$ mimic admin key create dave --quota 100000
key 'dave' created

  mk_9f3a7c2e1b8d4056a1f2e3c4b5a6d7e8

This token is shown once. Copy it now — the server stores only a hash.
```

The token is printed **exactly once, at creation time**. `mimic admin keys`
afterward shows only its prefix (`mk_9f3a7c2e`) — there is no way to recover
a lost token; revoke and re-mint instead.

**2. Send Dave his config.** Dave creates `~/.config/mimic/config.toml` with
your server's URL and **his own** token — never yours:

```toml
server_url = "http://your-server:8000"
token = "mk_9f3a7c2e1b8d4056a1f2e3c4b5a6d7e8"
```

At this point `mimic whoami` on Dave's machine shows role `user`, `0 / 100,000`
characters today, and `mimic clones` is empty — your voices are private by
default, so Dave can't see or use them yet, even by exact name.

**3. Share a voice with him.**

```bash
$ mimic share piper --to dave
shared piper with dave
```

**4. Dave uses it — with a qualified name.** A bare name resolves to a
caller's *own* voices first, so once Dave has voices of his own, `piper`
alone might mean something else (or nothing). Shared voices should always
be addressed as `owner/name`:

```bash
$ mimic clone say jim/piper "thanks for sharing this"
```

**5. Check what he's used.**

```bash
$ mimic admin usage --key dave
LABEL           REQUESTS    CHARS       AUDIO SECONDS
dave            3           412         18.2
```

**6. Revoke when you're done.**

```bash
$ mimic admin key revoke dave          # soft: key stops working, nothing deleted
$ mimic admin key revoke dave --purge  # also deletes every voice dave uploaded — irreversible
```

`--purge` asks for confirmation before it does anything, since it deletes
reference audio a friend may not be able to re-record.

### Notes

- The **root key** (the one from `MIMIC_API_TOKEN`) is shown in `mimic admin
  keys` marked with `*` — it cannot be revoked, purged, or demoted, since
  it's the recovery path if every minted admin key is lost.
- `role` is either `user` or `admin` — there's no in-between.
- `--quota 0` and `--max-voices 0` both mean **unlimited**, not zero
  allowance. `mimic admin keys` and `mimic whoami` render a 0 quota as
  `unlimited`.
- `mimic admin key rotate` does not exist by design — revoke and mint a new
  key instead.

## Library reference

### Sync

```python
from mimic import Client

with Client(server_url="http://localhost:8000", token=None) as c:
    audio = c.tts("hello", speaker="default")          # bytes
    c.tts_to_file("hello", "out.wav", speaker="default")
    c.clone_register("alice", "ref.wav", "transcript")
    cloned = c.clone_tts("alice", "now alice talks")
    one_shot = c.clone_oneshot("text", "ref.wav", "ref text")
    voices = c.list_voices()
    clones = c.list_clones()
    health = c.health()
```

### Async

Same surface, awaitable:

```python
from mimic import AsyncClient

async with AsyncClient() as c:
    audio = await c.tts("hello")
    await c.clone_register("alice", "ref.wav", "transcript")
```

### Errors

All HTTP errors raise subclasses of `mimic.errors.MimicError`:

- `MimicAuthError` (401)
- `MimicNotFoundError` (404)
- `MimicValidationError` (other 4xx)
- `MimicAPIError` (5xx and base for the above)

## Recording tips

- 5-15 seconds of clean speech is plenty.
- Read the printed script — varied phonemes give better cloning quality.
- Quiet room, mic close enough to avoid roominess.
- 24 kHz mono is what the recorder captures by default.
