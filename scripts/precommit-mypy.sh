#!/usr/bin/env bash
# pre-commit hook: the same two mypy runs as CI (app code strict; tests, migrations and eval/),
# with the API's venv interpreter. Never bare `python`: on Humayun's machine it is Cygwin's.
set -euo pipefail
cd "$(dirname "$0")/../apps/api"
if [ -x .venv/Scripts/python.exe ]; then
  py=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then
  py=.venv/bin/python
else
  echo "apps/api/.venv not found: create it first (docs/deployment.md)" >&2
  exit 1
fi
"$py" -m mypy
"$py" -m mypy tests alembic ../../eval
