# syntax=docker/dockerfile:1.7
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# System libs only — no system Python. uv installs its own standalone Python build
# (avoids the python3.12-not-in-jammy-default-repos problem).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libsndfile1 ca-certificates curl \
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

ENV MIMIC_DATA_DIR=/data \
    HF_HOME=/data/models \
    MIMIC_HOST=0.0.0.0 \
    MIMIC_PORT=8000

VOLUME ["/data"]
EXPOSE 8000

CMD ["uv", "run", "--package", "mimic-server", "mimic-server"]
