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
