# Self-hosting

## Docker (recommended)

```yaml
# docker-compose.yml
services:
  mimic-server:
    image: ghcr.io/jvogel/mimic-tts:latest
    ports: ["8000:8000"]
    volumes:
      - mimic-data:/data
    environment:
      MIMIC_LOG_LEVEL: INFO
      # MIMIC_API_TOKEN: change-me   # uncomment to require auth
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped

volumes:
  mimic-data:
```

`mimic-data` is where Qwen3-TTS weights cache (~7GB after first run) and
where reference recordings live. Treat it as durable state.

## From source

```bash
git clone https://github.com/jvogel/mimic-tts
cd mimic-tts
uv sync --package mimic-server
uv run mimic-server
```

Requires Python 3.12, an NVIDIA GPU, and CUDA 12.1.

## Reverse proxy + TLS

The server has no built-in TLS. Front it with caddy / nginx / traefik:

```caddyfile
mimic.example.com {
  reverse_proxy mimic-server:8000
}
```

Combine with `MIMIC_API_TOKEN` to expose it publicly.

## Pre-publish hygiene

If you fork this repo, scan for accidentally committed audio or secrets
before pushing public branches:

```bash
gitleaks detect --no-banner
git ls-files '*.wav' '*.mp3' '*.flac'
```

The provided `.gitignore` excludes `*.wav` at the repo root and the
`reference/` directory tree by default — but a fresh fork should still
audit once.
