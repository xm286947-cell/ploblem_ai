#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export STORAGE_LIFE_ALLOW_UNPINNED_RUNTIME=1
export STORAGE_LIFE_EXECUTION_MODE=runtime
export UNIFIED_AGENT_RUNTIME_ROOT="$ROOT/vendor/unified_agent_runtime"
export STORAGE_STRICT_PACKAGE_PROVENANCE=1
export STORAGE_WEB_HOST="${STORAGE_WEB_HOST:-0.0.0.0}"
export STORAGE_WEB_PORT="${STORAGE_WEB_PORT:-8765}"
export STORAGE_PRODUCT_TEST_MODE=real
export STORAGE_KNOWLEDGE_RELEASE_DIR="${STORAGE_KNOWLEDGE_RELEASE_DIR:-$ROOT/knowledge_release/current}"
export STORAGE_MODEL_CONFIG="${STORAGE_MODEL_CONFIG:-$ROOT/config/model.local.yaml}"
export PYTHONPATH="$ROOT:$UNIFIED_AGENT_RUNTIME_ROOT"
mkdir -p logs release
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "[setup] Creating package-local Python environment..."
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install -q --disable-pip-version-check -r "$ROOT/requirements.txt" -r "$UNIFIED_AGENT_RUNTIME_ROOT/requirements-runtime-p0-test.txt"
echo "[Storage R1] Starting canonical Storage product on http://127.0.0.1:$STORAGE_WEB_PORT"
( sleep 3; open "http://127.0.0.1:$STORAGE_WEB_PORT/" >/dev/null 2>&1 || true ) &
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/windows_start.py"
