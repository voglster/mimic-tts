# Plan 3 — Release Pipeline + Docs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up the public-release infrastructure for `mimic-tts`: lint script, release script, CI/CD workflows (PyPI Trusted Publishing + GHCR Docker push + AI-generated GitHub Release notes), README rewrite, deeper docs, NOTICE attribution. After this plan, a single `./release.sh patch` produces a tagged GitHub Release with a PyPI-published client wheel and a GHCR-published Docker image.

**Architecture:** Mirror the lumbergh release pattern. `release.sh` does preflight (clean tree + lint), bumps versions in **both** pyprojects, commits, tags, and pushes; GitHub Actions on `v*` tag handles PyPI publish (OIDC, no stored token), Docker buildx push to GHCR, and AI-generated release notes attached to a `gh release`. Lint script aggregates ruff format + ruff check + mypy across both packages, running tests separately to dodge the pytest collision.

**Tech Stack:** bash, GitHub Actions, `pypa/gh-action-pypi-publish`, `docker/build-push-action`, `gh` CLI.

**Source of truth:** `docs/superpowers/specs/2026-04-26-mimic-tts-design.md`.

---

### Task 1: Test layout cleanup + `lint.sh`

**Files:**
- Delete: `client/tests/__init__.py`
- Delete: `server/tests/__init__.py`
- Create: `lint.sh`

Drop `__init__.py` from both test directories so pytest can discover them in a single run (the package-style layout was an artifact of how the subagents scaffolded). This unlocks `pytest client/tests server/tests` and fixes the cross-package collision noted at the end of Plan 2.

- [ ] **Step 1: Delete the test `__init__.py` files**

```bash
git rm client/tests/__init__.py server/tests/__init__.py
```

- [ ] **Step 2: Verify combined test run works**

```bash
.venv/bin/pytest client/tests server/tests -q
```
Expected: all 71 tests pass with no module-name collision.

If a collision persists (e.g. duplicate `test_config.py` filenames across the two packages), uniqueify the filenames as a follow-up. The current set is:
- `client/tests/`: `test_base.py`, `test_client.py`, `test_async_client.py`, `test_client_config.py`, `test_recorder.py`, `test_cli.py`
- `server/tests/`: `test_app.py`, `test_auth.py`, `test_config.py`, `test_models.py`

`test_config.py` exists in both. If the combined run still errors, rename `client/tests/test_client_config.py` (already done above to avoid this) — confirm there are no other duplicates. **If a collision still occurs**, add `__init__.py` back to each `tests/` directory under unique names (`client_tests/`, `server_tests/`) is the wrong move; the right move is per-package pytest configs. Stop and report BLOCKED.

- [ ] **Step 3: Create `lint.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
errors=0

heading() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

cd "$ROOT"

heading "ruff format"
if ! .venv/bin/ruff format client server; then
  errors=1
fi

heading "ruff check --fix"
if ! .venv/bin/ruff check --fix client server; then
  errors=1
fi

heading "mypy (server)"
if ! .venv/bin/mypy server/mimic_server; then
  errors=1
fi

heading "mypy (client)"
if ! .venv/bin/mypy client/mimic; then
  errors=1
fi

heading "pytest (client)"
if ! .venv/bin/pytest client/tests -q; then
  errors=1
fi

heading "pytest (server)"
if ! .venv/bin/pytest server/tests -q; then
  errors=1
fi

echo
if [ "$errors" -ne 0 ]; then
  printf '\033[1;31mLint completed with errors.\033[0m\n'
  exit 1
else
  printf '\033[1;32mAll lints passed.\033[0m\n'
fi
```

Make it executable:
```bash
chmod +x lint.sh
```

- [ ] **Step 4: Run `lint.sh` and fix any new findings**

```bash
./lint.sh
```

Expected: passes. Pre-existing ruff findings (UP035 `Callable` from typing, FAST002 missing `Annotated`) may surface — fix them inline so the script can be a green gate going forward. If the fix touches files outside the lint script's responsibility, do it as a small follow-up commit ("style: ruff auto-fix").

- [ ] **Step 5: Stage**

```bash
git add lint.sh client/tests/__init__.py server/tests/__init__.py
git status --short
```
(The `git rm` already staged the deletions; this just adds the new script. Any auto-fixed source files from ruff also need to be staged.)

---

### Task 2: `release.sh`

**Files:**
- Create: `release.sh`

Adapted from lumbergh: preflight (clean tree + lint), bump version in both pyprojects, commit, tag, push, monitor workflows.

- [ ] **Step 1: Create `release.sh`**

```bash
#!/usr/bin/env bash
# Usage: ./release.sh [major|minor|patch] [-y]  (default: patch)
# Pass -y to skip confirmation prompt.
set -e

BUMP="patch"
SKIP_CONFIRM=""

for arg in "$@"; do
  case "$arg" in
    -y|--yes) SKIP_CONFIRM=1 ;;
    major|minor|patch) BUMP="$arg" ;;
    *) echo "Usage: $0 [major|minor|patch] [-y]"; exit 1 ;;
  esac
done

# --- Preflight checks ---

# 1. Abort if uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "❌ Uncommitted changes detected. Commit or stash before releasing."
  git status --short
  exit 1
fi

if [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "❌ Untracked files detected. Commit or remove before releasing."
  git ls-files --others --exclude-standard
  exit 1
fi

# 2. Lint check
echo "Running lint..."
if ! ./lint.sh > /tmp/mimic-release-lint.log 2>&1; then
  echo "❌ Lint failed. Fix errors before releasing:"
  tail -30 /tmp/mimic-release-lint.log
  exit 1
fi
echo "✅ Lint passed"

# --- Version bump ---

# Get latest stable tag (vX.Y.Z), default to v0.0.0 if none
LATEST=$(git tag -l 'v*' --sort=-v:refname | head -1)
LATEST="${LATEST:-v0.0.0}"

IFS='.' read -r MAJOR MINOR PATCH <<< "${LATEST#v}"
case "$BUMP" in
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  patch) PATCH=$((PATCH + 1)) ;;
esac
NEW_VERSION="v${MAJOR}.${MINOR}.${PATCH}"
NEW_PEP="${MAJOR}.${MINOR}.${PATCH}"

echo "Releasing $LATEST -> $NEW_VERSION"

if [[ -z "$SKIP_CONFIRM" ]]; then
  read -r -p "Continue? [y/N] " answer
  if [[ ! "$answer" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
  fi
fi

# Write the new version into both pyprojects.
python3 - "$NEW_PEP" <<'PY'
import re, sys, pathlib
ver = sys.argv[1]
for path in [
    pathlib.Path("client/pyproject.toml"),
    pathlib.Path("server/pyproject.toml"),
]:
    text = path.read_text()
    new = re.sub(r'(?m)^version = ".*"$', f'version = "{ver}"', text, count=1)
    if new == text:
        sys.exit(f"could not bump version in {path}")
    path.write_text(new)
print(f"bumped {ver}")
PY

# Also bump the client _version.py for hatch-version sources.
python3 - "$NEW_PEP" <<'PY'
import re, sys, pathlib
ver = sys.argv[1]
path = pathlib.Path("client/mimic/_version.py")
text = path.read_text()
new = re.sub(r'__version__ = ".*"', f'__version__ = "{ver}"', text)
path.write_text(new)
PY

git add client/pyproject.toml server/pyproject.toml client/mimic/_version.py
git commit -m "chore: release ${NEW_VERSION}"

git tag "$NEW_VERSION"
git push origin HEAD
git push origin "$NEW_VERSION"
echo "Tag $NEW_VERSION pushed."

# --- Monitor workflows ---

echo ""
echo "Waiting for GitHub Actions..."

TAG_SHA=$(git rev-parse "$NEW_VERSION")
MAX_WAIT=900  # 15 minutes (Docker builds + PyPI publish)
POLL_INTERVAL=15
WAITED=0

while [ $WAITED -lt $MAX_WAIT ]; do
  sleep $POLL_INTERVAL
  WAITED=$((WAITED + POLL_INTERVAL))

  RUNS=$(gh run list --commit "$TAG_SHA" --limit 10 --json name,status,conclusion,url 2>/dev/null || echo "[]")

  TOTAL=$(echo "$RUNS" | jq length)
  COMPLETED=$(echo "$RUNS" | jq '[.[] | select(.status == "completed")] | length')
  FAILED=$(echo "$RUNS" | jq '[.[] | select(.conclusion == "failure")] | length')

  if [ "$TOTAL" -eq 0 ]; then
    continue
  fi

  if [ "$FAILED" -gt 0 ]; then
    echo ""
    echo "❌ Workflow failure detected:"
    echo "$RUNS" | jq -r '.[] | select(.conclusion == "failure") | "  \(.name): \(.url)"'
    exit 1
  fi

  if [ "$COMPLETED" -eq "$TOTAL" ]; then
    echo ""
    echo "✅ All workflows passed:"
    echo "$RUNS" | jq -r '.[] | "  \(.name): \(.conclusion)"'
    echo ""
    echo "🎉 $NEW_VERSION released successfully!"
    exit 0
  fi

  printf "."
done

echo ""
echo "⚠️  Timed out waiting for workflows (${MAX_WAIT}s). Check manually:"
echo "  https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/actions"
exit 1
```

Make it executable:
```bash
chmod +x release.sh
```

- [ ] **Step 2: Smoke test the version-bump logic without pushing**

```bash
# Dry-run check: run the bump python on a temp copy.
cp client/pyproject.toml /tmp/cp_check.toml
cp server/pyproject.toml /tmp/sp_check.toml
python3 - "9.9.9" <<'PY'
import re, sys, pathlib
ver = sys.argv[1]
for path in [pathlib.Path("/tmp/cp_check.toml"), pathlib.Path("/tmp/sp_check.toml")]:
    text = path.read_text()
    new = re.sub(r'(?m)^version = ".*"$', f'version = "{ver}"', text, count=1)
    assert new != text, f"failed for {path}"
print("bump regex works")
PY
```
Expected: `bump regex works`. Clean up:
```bash
rm /tmp/cp_check.toml /tmp/sp_check.toml
```

Do NOT actually run `./release.sh` — that would tag and push.

- [ ] **Step 3: Stage**

```bash
git add release.sh
git status --short
```

---

### Task 3: CI workflow (PR + main)

**Files:**
- Create: `.github/workflows/ci.yml`

Runs on every PR and every push to `main`. Lints + tests; does NOT publish.

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
      - name: Sync workspace
        run: uv sync --all-packages --all-extras
      - name: Ruff format check
        run: uv run ruff format --check client server
      - name: Ruff lint
        run: uv run ruff check client server
      - name: Mypy server
        run: uv run mypy server/mimic_server
      - name: Mypy client
        run: uv run mypy client/mimic
      - name: Pytest client
        run: uv run pytest client/tests -q
      - name: Pytest server
        # Server tests don't actually load Qwen models (loader is injected).
        # The default `app = build_app(Settings())` at import time DOES NOT load
        # models — it just registers them. So this should pass on CI runners
        # without a GPU.
        run: uv run pytest server/tests -q

  build-wheel:
    runs-on: ubuntu-latest
    needs: lint-and-test
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
      - name: Build client wheel
        run: uv build --package mimic-tts --out-dir dist
      - name: Upload wheel artifact
        uses: actions/upload-artifact@v4
        with:
          name: mimic-tts-wheel
          path: dist/*.whl
```

- [ ] **Step 2: Stage**

```bash
git add .github/workflows/ci.yml
git status --short
```

(No way to test this locally without `act`; verification happens on the first PR.)

---

### Task 4: Release workflow (PyPI + GHCR + GH Release)

**Files:**
- Create: `.github/workflows/release.yml`

Triggered on `v*` tag push. Builds the client wheel, publishes to PyPI via OIDC, builds the Docker image and pushes to GHCR, generates AI release notes, creates a GitHub Release with the wheel attached.

- [ ] **Step 1: Create `.github/workflows/release.yml`**

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: write       # gh release create
  id-token: write       # PyPI Trusted Publishing OIDC
  packages: write       # GHCR push

jobs:
  build-wheel:
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.version.outputs.version }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
      - id: version
        run: echo "version=${GITHUB_REF_NAME#v}" >> "$GITHUB_OUTPUT"
      - name: Build client wheel
        run: uv build --package mimic-tts --out-dir dist
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/*

  publish-pypi:
    runs-on: ubuntu-latest
    needs: build-wheel
    environment: pypi
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: dist/

  publish-docker:
    runs-on: ubuntu-latest
    needs: build-wheel
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Lowercase image owner
        id: img
        run: echo "owner=$(echo '${{ github.repository_owner }}' | tr '[:upper:]' '[:lower:]')" >> "$GITHUB_OUTPUT"
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: server/Dockerfile
          push: true
          tags: |
            ghcr.io/${{ steps.img.outputs.owner }}/mimic-tts:${{ github.ref_name }}
            ghcr.io/${{ steps.img.outputs.owner }}/mimic-tts:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  github-release:
    runs-on: ubuntu-latest
    needs: [publish-pypi, publish-docker]
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist
      - name: Generate AI release notes (optional)
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          LLM_BASE_URL: ${{ secrets.LLM_BASE_URL }}
          LLM_MODEL: ${{ secrets.LLM_MODEL }}
        run: |
          if [ -z "${LLM_API_KEY:-}" ]; then
            echo "LLM_API_KEY not set; using minimal notes."
            echo "Release ${{ github.ref_name }}." > RELEASE_NOTES.md
            exit 0
          fi
          PREV=$(git tag -l 'v*' --sort=-v:refname | sed -n '2p')
          PREV="${PREV:-$(git rev-list --max-parents=0 HEAD)}"
          COMMITS="$(git log --oneline ${PREV}..HEAD)"
          STATS="$(git diff --stat ${PREV}..HEAD)"
          python3 - <<PY > RELEASE_NOTES.md
          import json, urllib.request, os, sys
          base = os.environ['LLM_BASE_URL'].rstrip('/')
          prompt = f'''Generate concise release notes for mimic-tts ${{{{ github.ref_name }}}}.
          mimic-tts is a self-hosted Qwen3-TTS voice cloning + synthesis service with a Python client.
          Commits since last release:
          {os.environ.get("COMMITS","")}
          Diff stats:
          {os.environ.get("STATS","")}
          Format: brief intro, then bulleted list grouped by Features / Fixes / Other. Keep it short.'''
          body = json.dumps({
            'model': os.environ['LLM_MODEL'],
            'max_tokens': 1024,
            'messages': [{'role': 'user', 'content': prompt}]
          }).encode()
          req = urllib.request.Request(f'{base}/chat/completions',
            data=body,
            headers={'Authorization': f"Bearer {os.environ['LLM_API_KEY']}",
                     'Content-Type': 'application/json'})
          resp = json.loads(urllib.request.urlopen(req).read())
          print(resp['choices'][0]['message']['content'])
          PY
        # COMMITS/STATS need to be exported for the python heredoc:
      - name: Export COMMITS/STATS
        if: always()
        run: |
          PREV=$(git tag -l 'v*' --sort=-v:refname | sed -n '2p')
          PREV="${PREV:-$(git rev-list --max-parents=0 HEAD)}"
          {
            echo "COMMITS<<EOF"
            git log --oneline ${PREV}..HEAD
            echo "EOF"
            echo "STATS<<EOF"
            git diff --stat ${PREV}..HEAD
            echo "EOF"
          } >> "$GITHUB_ENV"
      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          test -f RELEASE_NOTES.md || echo "Release ${{ github.ref_name }}." > RELEASE_NOTES.md
          gh release create "${{ github.ref_name }}" dist/*.whl \
            --title "${{ github.ref_name }}" \
            --notes-file RELEASE_NOTES.md
```

The AI release notes step is best-effort: if `LLM_API_KEY` isn't set in repo secrets, it falls back to a one-line note. The `Export COMMITS/STATS` step exists so the `python3 -` heredoc can read git history that's been pre-extracted (the inline expansion in the previous step was a placeholder).

**NOTE:** The two-step COMMITS/STATS handling above is awkward. If you find a cleaner way to do this in one step (e.g. write commit list + stats to files first, then read from files in the python script), refactor — but keep the fallback path when the LLM secrets are absent.

- [ ] **Step 2: Stage**

```bash
git add .github/workflows/release.yml
git status --short
```

---

### Task 5: Privacy/publish hygiene — `NOTICE` and `.gitignore` review

**Files:**
- Create: `NOTICE`
- Modify: `.gitignore` (final pass)

- [ ] **Step 1: Create `NOTICE`**

```
mimic-tts
Copyright (c) 2026 Jim Vogel
Licensed under the MIT License (see LICENSE).

This product builds on:
- Qwen3-TTS by the Qwen Team (Alibaba):
    https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base
    https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
  Models are downloaded at runtime to a user-controlled cache directory and
  are not redistributed by this project. See the upstream model cards for
  their license terms.

- FastAPI, httpx, typer, sounddevice, soundfile, numpy, pydantic-settings,
  platformdirs — all under their respective open-source licenses.

This project does not transmit voice recordings to any third party.
Reference recordings registered through `/clone/register` are stored locally
in the directory configured via MIMIC_REFERENCE_DIR (default: ./reference).
```

- [ ] **Step 2: Confirm `.gitignore` is complete**

Read the current `.gitignore`. Confirm it covers:
- `__pycache__/`, `*.pyc`, `.venv/`, `dist/`, `*.egg-info/`
- `server/reference/*` and `reference/*` (with `!*.gitkeep` exceptions)
- `.env`, `.env.local`
- `*.wav` at the repo root
- `.DS_Store`, `.idea/`, `.vscode/`

If anything is missing, add it. (No expected changes — the Plan 1 setup was thorough.)

- [ ] **Step 3: Stage**

```bash
git add NOTICE .gitignore
git status --short
```

---

### Task 6: README rewrite

**Files:**
- Rewrite: `README.md`
- Create: `LICENSE` (if not already present — confirm first)

Replace the legacy Qwen3-TTS-API README with a mimic-tts landing page. Two side-by-side quickstarts, links to deeper docs, privacy callout, voice list.

- [ ] **Step 1: Confirm `LICENSE`**

```bash
ls LICENSE 2>/dev/null
```

If missing, create it as MIT:

```
MIT License

Copyright (c) 2026 Jim Vogel

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Replace `README.md`**

```markdown
# mimic-tts

Self-hosted **Qwen3-TTS** voice cloning + synthesis. Runs on your own GPU,
ships with a tiny Python client, never sends a recording off your machine.

- 🐳 **Server** — Docker image with on-demand model loading + idle unload.
- 📦 **Client** — `pip install mimic-tts` for a `mimic` CLI and a Python
  library (sync **and** async).
- 🎙️ **Voice cloning** — record a 10-second sample, register it, synthesize.
- 🔒 **Optional bearer auth** — single env var flips it on.

## Quick start (server, Docker)

```bash
docker run --gpus all \
  -p 8000:8000 \
  -v mimic-data:/data \
  ghcr.io/voglster/mimic-tts:latest
```

Or with `docker-compose.yml` from this repo:

```bash
docker compose up
```

Requires NVIDIA GPU + nvidia-container-toolkit. First run downloads ~7GB of
Qwen3-TTS weights into the `/data` volume; subsequent runs are fast.

## Quick start (client)

```bash
pip install mimic-tts

mimic say "hello, this is a test" --out hello.wav
mimic record alice                       # interactive: record + register a clone
mimic clone say alice "now I sound like alice"
mimic voices                             # list built-in voices
mimic clones                             # list registered clones
```

The client reads `MIMIC_SERVER_URL` from the environment, or
`~/.config/mimic/config.toml`:

```toml
server_url = "http://nas.local:8000"
token = "optional-bearer-token"
default_voice = "Ryan"
```

### Python library

```python
# Sync
from mimic import Client
with Client() as c:
    c.tts_to_file("hello there", "out.wav", speaker="Ryan")

# Async
from mimic import AsyncClient
async with AsyncClient() as c:
    audio = await c.tts("hello there")
```

## Endpoints (server)

| Method | Path                | What it does                                |
|--------|---------------------|---------------------------------------------|
| `POST` | `/tts`              | Built-in voice TTS (drop-in for `edge-tts`) |
| `POST` | `/clone/register`   | Register a reference voice (file + transcript) |
| `POST` | `/clone/tts`        | Synthesize using a registered clone         |
| `POST` | `/clone/oneshot`    | Clone + synthesize in one call              |
| `GET`  | `/voices`           | List built-in voices                        |
| `GET`  | `/clone/voices`     | List registered clone voices                |
| `GET`  | `/health`           | Loaded models + registered voices (always open) |

Set `MIMIC_API_TOKEN=...` to require `Authorization: Bearer ...` on every
endpoint except `/health`. Off by default.

## Built-in voices

| Voice      | Language |
|------------|----------|
| Ryan, Aiden | English |
| Vivian, Serena, Uncle_Fu, Dylan, Eric | Chinese |
| Ono_Anna   | Japanese |
| Sohee      | Korean   |

## Privacy

Reference recordings are stored locally on the server in
`MIMIC_REFERENCE_DIR` (default: `./reference`, or `/data/reference` in
Docker). They are not transmitted anywhere. The `.gitignore` excludes them
by default — you would have to add them deliberately to commit them.

## Documentation

- [Server reference](docs/server.md) — env vars, endpoints, GPU notes
- [Client reference](docs/client.md) — CLI, library, recording tips
- [Self-hosting guide](docs/self-hosting.md) — docker-compose, reverse proxy, auth

## Acknowledgements

Built on top of [Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base)
by the Qwen Team. See `NOTICE` for full attribution.

## License

MIT. See `LICENSE`.
```

- [ ] **Step 3: Stage**

```bash
git add README.md LICENSE
git status --short
```

---

### Task 7: `docs/server.md`, `docs/client.md`, `docs/self-hosting.md`, `CONTRIBUTING.md`

**Files:**
- Create: `docs/server.md`
- Create: `docs/client.md`
- Create: `docs/self-hosting.md`
- Create: `CONTRIBUTING.md`

These are reference documents linked from the README. Keep them practical, not exhaustive — they should answer the questions someone has *while running into a problem*, not document every line of code.

- [ ] **Step 1: Create `docs/server.md`**

```markdown
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
| `MIMIC_UNLOAD_AFTER` | `15` | `15` | Seconds idle before models unload |
| `MIMIC_API_TOKEN` | unset | unset | Optional bearer token (off by default) |
| `MIMIC_LOG_LEVEL` | `INFO` | `INFO` | Log level |

## Endpoints

All endpoints accept and return form-encoded data unless noted. Audio
responses are `audio/wav`.

### `POST /tts` — built-in voices
Form fields: `text` (required), `language` (default `English`), `speaker`
(default `Ryan`), `instruct` (optional style cue).

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

### `GET /voices`, `GET /clone/voices`, `GET /health`
JSON lists. `/health` is always unauthenticated.

## GPU + memory

Qwen3-TTS loads in `bfloat16` on `cuda:0`. Each model takes ~6GB VRAM. The
server unloads any unused model after `MIMIC_UNLOAD_AFTER` seconds idle so
you can share the GPU with other workloads (e.g. a local Ollama).

First call after idle takes ~10s for the model to load; subsequent calls
are fast.

## Auth

`MIMIC_API_TOKEN=secret` flips on bearer auth for every endpoint except
`/health`. The check uses `secrets.compare_digest` (constant-time).

There's intentionally no token rotation, no per-user tokens, no JWT, and no
TLS termination — that's your reverse proxy's job. See
[self-hosting](self-hosting.md).
```

- [ ] **Step 2: Create `docs/client.md`**

```markdown
# Client reference

`pip install mimic-tts` gives you a `mimic` CLI and an importable
`mimic` Python library (sync + async).

## Configuration

The client resolves config in this order, first match wins:

1. Constructor kwargs / CLI flags
2. Env vars: `MIMIC_SERVER_URL`, `MIMIC_API_TOKEN`
3. `~/.config/mimic/config.toml` (cross-platform via platformdirs)
4. Defaults (`http://localhost:8000`, no token, default voice `Ryan`)

Example `config.toml`:

```toml
server_url = "http://nas.local:8000"
token = "optional"
default_voice = "Aiden"
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
    audio = c.tts("hello", speaker="Ryan")          # bytes
    c.tts_to_file("hello", "out.wav", speaker="Ryan")
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
```

- [ ] **Step 3: Create `docs/self-hosting.md`**

```markdown
# Self-hosting

## Docker (recommended)

```yaml
# docker-compose.yml
services:
  mimic-server:
    image: ghcr.io/voglster/mimic-tts:latest
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
git clone https://github.com/voglster/mimic-tts
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
```

- [ ] **Step 4: Create `CONTRIBUTING.md`**

```markdown
# Contributing

## Dev setup

Requires Python 3.12 and `uv`.

```bash
git clone https://github.com/voglster/mimic-tts
cd mimic-tts
uv sync --all-packages
```

This installs both `mimic-server` (the Docker workspace member) and
`mimic-tts` (the PyPI client) in editable mode in a shared `.venv`.

## Tests

```bash
.venv/bin/pytest client/tests
.venv/bin/pytest server/tests
```

Or run everything via `./lint.sh`, which also runs ruff + mypy.

## Code style

- Ruff with the lumbergh ruleset (full lint selection, `max-complexity = 10`).
- Mypy with loose typing (annotations welcome but not required).
- The complexity cap is load-bearing — if you find yourself bumping it,
  decompose the function instead.
- Don't write trailing summaries or restate what code does in comments.

## Releasing

```bash
./release.sh patch     # or minor / major
```

This bumps both pyprojects, commits, tags `vX.Y.Z`, pushes, and waits for
GitHub Actions. PyPI publishing uses Trusted Publishing (OIDC) — no token
in repo secrets. GHCR uses `GITHUB_TOKEN`.

## Architecture

- `server/mimic_server/` — FastAPI app, model manager, optional bearer auth.
- `client/mimic/` — sync + async clients, recorder, typer CLI.
- `docs/superpowers/specs/` — design specs.
- `docs/superpowers/plans/` — implementation plans.
```

- [ ] **Step 5: Stage**

```bash
git add docs/server.md docs/client.md docs/self-hosting.md CONTRIBUTING.md
git status --short
```

---

## End-of-plan verification

- [ ] `./lint.sh` passes locally
- [ ] All 71 tests pass: `.venv/bin/pytest client/tests server/tests -q`
- [ ] `mimic --help` works
- [ ] `release.sh --help` documents itself (or running with no args defaults to `patch` and prompts before tagging)
- [ ] `.github/workflows/ci.yml` and `release.yml` parse (`gh workflow view` after first push, or YAML lint locally)
- [ ] README, NOTICE, LICENSE, CONTRIBUTING.md all exist
- [ ] `docs/server.md`, `docs/client.md`, `docs/self-hosting.md` all exist

## Manual steps before first release

1. Make the GitHub repo public.
2. On PyPI: claim `mimic-tts` and configure Trusted Publishing
   ([guide](https://docs.pypi.org/trusted-publishers/)). Bind it to:
   - Repo: `<owner>/mimic-tts`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. (Optional) In GitHub repo settings → Secrets, add `LLM_API_KEY`,
   `LLM_BASE_URL`, `LLM_MODEL` for AI release notes.
4. Run `./release.sh patch` (will create `v0.0.1`).

## State after this plan

- The repo is publicly releasable: tag → PyPI + GHCR + GH Release in one go.
- Both packages share a single version, bumped together.
- README + docs explain server, client, self-hosting, contributing.
- NOTICE attributes Qwen3-TTS upstream.
- mimic-tts rebrand is **complete**.
