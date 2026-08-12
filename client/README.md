# mimic-tts

Python client and CLI for the [mimic-tts](https://github.com/voglster/mimic-tts) server.

```bash
pip install mimic-tts
```

## Admin and sharing

If your key is an admin key, you can mint keys for other people, share
voices with them, and check their usage:

```bash
mimic admin key create dave --quota 100000       # prints dave's token once
mimic share jim/piper --to dave                  # let dave use your voice
mimic admin usage --key dave                      # check what dave has used
mimic admin key revoke dave                       # soft revoke (or --purge)
```

Give `dave` a `~/.config/mimic/config.toml` with **his own** token:

```toml
server_url = "http://your-server:8000"
token = "<the token from `admin key create`>"
```

Dave should refer to voices shared with him by their **qualified name**
(`jim/piper`) — a bare name resolves to his own voices first.

See [`docs/client.md`](https://github.com/voglster/mimic-tts/blob/main/docs/client.md#admin-and-sharing)
for the full worked example.

Full documentation: <https://github.com/voglster/mimic-tts>
