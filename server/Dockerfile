# syntax=docker/dockerfile:1.7

# --- web build stage: Vite/React → static assets served by FastAPI ---
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- runtime stage ---
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# System libs only — no system Python. uv installs its own standalone Python build
# (avoids the python3.12-not-in-jammy-default-repos problem).
# ffmpeg is here for transcoding browser uploads (WebM/Opus, MP4/AAC) to the
# WAV the TTS backend expects on /clone/register.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libsndfile1 ffmpeg ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Workspace root + server pyproject for dependency-only caching layer.
# uv lockfile lives at the workspace root.
COPY pyproject.toml uv.lock ./
COPY server/pyproject.toml ./server/pyproject.toml
RUN uv sync --frozen --no-dev --package mimic-server --no-install-project

# Source
COPY server/mimic_server ./server/mimic_server
RUN uv sync --frozen --no-dev --package mimic-server

# Built web UI (served via StaticFiles when MIMIC_WEB_DIST points at it).
COPY --from=web /web/dist /app/web/dist

ENV MIMIC_DATA_DIR=/data \
    HF_HOME=/data/models \
    MIMIC_HOST=0.0.0.0 \
    MIMIC_PORT=8000 \
    MIMIC_WEB_DIST=/app/web/dist

VOLUME ["/data"]
EXPOSE 8000

CMD ["uv", "run", "--package", "mimic-server", "mimic-server"]
