# Contributing

## Dev setup

Requires Python 3.12 and `uv`.

```bash
git clone https://github.com/jvogel/mimic-tts
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
