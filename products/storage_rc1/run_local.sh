#!/bin/sh
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

export STORAGE_LIFE_EXECUTION_MODE="${STORAGE_LIFE_EXECUTION_MODE:-runtime}"
export STORAGE_WEB_HOST="${STORAGE_WEB_HOST:-127.0.0.1}"
export STORAGE_WEB_PORT="${STORAGE_WEB_PORT:-8765}"

if [ "$STORAGE_LIFE_EXECUTION_MODE" = "runtime" ]; then
  if [ -z "${UNIFIED_AGENT_RUNTIME_ROOT:-}" ]; then
    if [ -f "$ROOT/vendor/unified_agent_runtime/runtime/__init__.py" ]; then
      UNIFIED_AGENT_RUNTIME_ROOT="$ROOT/vendor/unified_agent_runtime"
    elif [ -f "$ROOT/../../runtime/__init__.py" ]; then
      UNIFIED_AGENT_RUNTIME_ROOT="$(cd "$ROOT/../.." && pwd)"
    else
      echo "ERROR: runtime mode requires UNIFIED_AGENT_RUNTIME_ROOT or packaged vendor/unified_agent_runtime." >&2
      exit 2
    fi
  fi
  export UNIFIED_AGENT_RUNTIME_ROOT
  export STORAGE_MODEL_CONFIG="${STORAGE_MODEL_CONFIG:-$ROOT/config/model.local.yaml}"
  export PYTHONPATH="$ROOT:$UNIFIED_AGENT_RUNTIME_ROOT${PYTHONPATH:+:$PYTHONPATH}"
else
  echo "[compat] STORAGE_LIFE_EXECUTION_MODE=$STORAGE_LIFE_EXECUTION_MODE (legacy mode is explicit compatibility only)" >&2
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
fi

PYTHON_BIN="$(sh "$ROOT/scripts/select_python.sh")"
python_status=$?
if [ "$python_status" -ne 0 ]; then
  echo "ERROR: unable to prepare Python environment." >&2
  exit "$python_status"
fi

if [ "$STORAGE_LIFE_EXECUTION_MODE" = "runtime" ]; then
  "$PYTHON_BIN" "$ROOT/scripts/effective_runtime_config.py" || exit $?
fi

child_pid=""
cleanup() {
  status=$?
  trap - EXIT
  if [ -n "${child_pid:-}" ] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit "$status"
}
handle_int() {
  trap - INT
  if [ -n "${child_pid:-}" ] && kill -0 "$child_pid" 2>/dev/null; then
    kill -INT "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  child_pid=""
  exit 130
}
handle_term() {
  trap - TERM
  if [ -n "${child_pid:-}" ] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  child_pid=""
  exit 143
}
trap cleanup EXIT
trap handle_int INT
trap handle_term TERM

echo "Storage: http://$STORAGE_WEB_HOST:$STORAGE_WEB_PORT"
echo "Runtime status: http://127.0.0.1:$STORAGE_WEB_PORT/api/v1/runtime/status"
echo "Routing example: http://127.0.0.1:$STORAGE_WEB_PORT/api/v1/runtime/route?device_type=SSD"

"$PYTHON_BIN" -m uvicorn storage_life.app:app --host "$STORAGE_WEB_HOST" --port "$STORAGE_WEB_PORT" &
child_pid=$!
wait "$child_pid"
status=$?
child_pid=""
trap - EXIT INT TERM
exit "$status"
