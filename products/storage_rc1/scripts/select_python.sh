#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
check_python() {
  candidate="$1"
  "$candidate" - <<'PY' >/dev/null 2>&1
import sys
assert sys.version_info >= (3, 11)
import fastapi, uvicorn, pypdf, multipart, reportlab, httpx, PIL, pytest, yaml, pdfplumber, pydantic, jsonschema
PY
}

if command -v python3 >/dev/null 2>&1 && check_python python3; then
  printf '%s\n' "$(command -v python3)"
  exit 0
fi

if [ -x .venv/bin/python ] && check_python .venv/bin/python; then
  printf '%s\n' "$PWD/.venv/bin/python"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: Python 3.11+ is required." >&2
  exit 2
fi

echo "[bootstrap] required Python modules are missing; creating local .venv" >&2
python3 -m venv .venv
if ! .venv/bin/python -m pip install -q -r requirements.txt; then
  echo "ERROR: dependency installation failed. If this server has no Internet, preinstall requirements.txt before testing." >&2
  exit 2
fi
if ! check_python .venv/bin/python; then
  echo "ERROR: Python dependency verification failed after installation." >&2
  exit 2
fi
printf '%s\n' "$PWD/.venv/bin/python"
