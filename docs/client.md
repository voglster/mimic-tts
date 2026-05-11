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
```

The interactive `mimic record <name>` flow:

1. Prints a 4-sentence script chosen for varied phonemes.
2. "Press Enter to start recording, Ctrl+C to abort."
3. Records from the default mic until you press Enter again (cap 30s).
4. Plays back the take.
5. "Keep this take? [y/N/r=retry]" — `r` re-records, `y` keeps.
6. Asks for the transcript (defaulting to the printed script).
7. POSTs to `/clone/register`.

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
