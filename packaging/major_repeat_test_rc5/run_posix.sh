#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
EXPECTED_PLATFORM="${EXPECTED_PLATFORM:-}"
ACTUAL_PLATFORM=$(uname -s 2>/dev/null | tr '[:upper:]' '[:lower:]')
case "$ACTUAL_PLATFORM" in linux*) ACTUAL_PLATFORM=linux;; darwin*) ACTUAL_PLATFORM=darwin;; esac
if [ -n "$EXPECTED_PLATFORM" ] && [ "$ACTUAL_PLATFORM" != "$EXPECTED_PLATFORM" ]; then echo "Platform mismatch: expected $EXPECTED_PLATFORM, got $ACTUAL_PLATFORM"; exit 2; fi
choose_python(){ for candidate in python3.11 python3 python; do if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then printf '%s\n' "$candidate"; return 0; fi; done; return 1; }
PYTHON_BIN=$(choose_python || true)
if [ -z "$PYTHON_BIN" ]; then echo "Python 3.11+ is required."; exit 1; fi
VENV_PY="$SCRIPT_DIR/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then "$PYTHON_BIN" -m venv "$SCRIPT_DIR/.venv"; "$VENV_PY" -m pip install --disable-pip-version-check -r "$SCRIPT_DIR/app/requirements.txt"; fi
mkdir -p "$SCRIPT_DIR/data/runtime"
cd "$SCRIPT_DIR/app"
"$VENV_PY" scripts/major_repeat_test_rc5.py smoke
echo "Starting existing Quality Platform Web at http://127.0.0.1:8080/p0/major-production"
exec "$VENV_PY" scripts/major_repeat_test_rc5.py serve "$@"
