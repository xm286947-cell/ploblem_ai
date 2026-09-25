#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export STORAGE_LIFE_EXECUTION_MODE=runtime
export UNIFIED_AGENT_RUNTIME_ROOT="$ROOT/vendor/unified_agent_runtime"
export STORAGE_STRICT_PACKAGE_PROVENANCE=1
export STORAGE_MODEL_CONFIG="${STORAGE_MODEL_CONFIG:-$ROOT/config/model.local.yaml}"
export RUNTIME_PROVIDER_TRACE=1
export RUNTIME_PROVIDER_DIAGNOSTICS=1
export RUNTIME_PROVIDER_TRACE_FILE="$ROOT/logs/provider_runtime.log"
export STORAGE_RUNTIME_HTTP_TRACE=1
export STORAGE_APP_LOG_STDOUT=1
export STORAGE_WEB_HOST="${STORAGE_WEB_HOST:-0.0.0.0}"
export STORAGE_WEB_PORT="${STORAGE_WEB_PORT:-8765}"
export STORAGE_PRODUCT_TEST_MODE=real

mkdir -p "$ROOT/logs" "$ROOT/release"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "[setup] Creating package-local Python environment..."
  python3 -m venv "$ROOT/.venv"
fi

PY="$ROOT/.venv/bin/python"
export VIRTUAL_ENV="$ROOT/.venv"
unset PYTHONHOME || true
export PYTHONPATH="$ROOT:$UNIFIED_AGENT_RUNTIME_ROOT"
export PATH="$VIRTUAL_ENV/bin:$PATH"

echo "[setup] Checking dependencies..."
"$PY" -m pip install -q --disable-pip-version-check \
  -r "$ROOT/requirements.txt" \
  -r "$UNIFIED_AGENT_RUNTIME_ROOT/requirements-runtime-p0-test.txt"

echo "============================================================"
echo "STORAGE V1.12 - MAC NORMAL STARTUP"
echo "PRODUCT_E2E=NOT_RUN"
echo "MOCK=NOT_RUN"
echo "FAULT_TESTS=NOT_RUN"
echo "NETWORK_OWNER=UNIFIED_AGENT_RUNTIME_ONLY"
echo "WEB=http://127.0.0.1:$STORAGE_WEB_PORT"
echo "============================================================"

# V1.12 windows_start.py is otherwise cross-platform, but its DualWriter lacks
# the TTY method Uvicorn expects on macOS. Patch that compatibility in memory;
# no package source file is modified.
exec "$PY" - <<'PY'
import sys
from scripts import windows_start as startup

def _isatty(self):
    for stream in self.streams:
        try:
            if stream.isatty():
                return True
        except Exception:
            pass
    return False

startup.DualWriter.isatty = _isatty
raise SystemExit(startup.main())
PY
