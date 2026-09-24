#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p logs release
: > release/storage_app.log
: > release/openai_mock.log
: > release/mock_router.log
rm -f release/PRODUCT_E2E_RESULT.json release/PRODUCT_FAILURE_E2E_RESULT.json

MODE="${1:-mock}"
WEB_HOST="${STORAGE_WEB_HOST:-127.0.0.1}"
WEB_PORT="${STORAGE_WEB_PORT:-8765}"
OPENAI_MOCK_PORT="${STORAGE_OPENAI_MOCK_PORT:-18000}"
MOCK_ROUTER_PORT="${STORAGE_MOCK_ROUTER_PORT:-18001}"
INTERNAL_BASE="http://127.0.0.1:${WEB_PORT}"
PUBLIC_URL="${STORAGE_PUBLIC_URL:-}"

if [ "$MODE" != "mock" ] && [ "$MODE" != "real" ]; then
  echo "ERROR mode must be mock or real" >&2
  exit 2
fi

PORT_GUARD_PYTHON="${STORAGE_PYTHON_BIN:-}"
if [ -z "$PORT_GUARD_PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PORT_GUARD_PYTHON="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PORT_GUARD_PYTHON="$(command -v python)"
  else
    echo "PORT_CHECK_FAILED" >&2
    echo "REASON=PYTHON_NOT_FOUND" >&2
    echo "PRODUCT_E2E=NOT_RUN" >&2
    exit 97
  fi
fi

guard_free(){
  host="$1"; port="$2"; service="$3"
  set +e
  "$PORT_GUARD_PYTHON" scripts/port_guard.py check-free --host "$host" --port "$port" --service "$service"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  echo "PRODUCT_E2E=NOT_RUN" >&2
  exit "$rc"
}

# P0 port ownership gate. No product/mock service is spawned before all required
# ports for this mode are proven free.
guard_free "$WEB_HOST" "$WEB_PORT" "Storage-Web"
if [ "$MODE" = "mock" ]; then
  guard_free "127.0.0.1" "$OPENAI_MOCK_PORT" "OpenAI-Mock"
  guard_free "127.0.0.1" "$MOCK_ROUTER_PORT" "Storage-Mock-Router"
fi

RUNTIME_ROOT="${UNIFIED_AGENT_RUNTIME_ROOT:-${2:-}}"
if [ -z "$RUNTIME_ROOT" ]; then
  RUNTIME_ROOT="$(bash ./scripts/setup_runtime.sh)"
fi
export UNIFIED_AGENT_RUNTIME_ROOT="$RUNTIME_ROOT"

PYTHON_BIN="${STORAGE_PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then PYTHON_BIN="$(bash ./scripts/select_python.sh)"; fi
export PYTHONPATH="$RUNTIME_ROOT:${PYTHONPATH:-}"
export STORAGE_LIFE_EXECUTION_MODE=runtime
export STORAGE_PRODUCT_TEST_MODE="$MODE"
export STORAGE_RUNTIME_HTTP_TRACE="${STORAGE_RUNTIME_HTTP_TRACE:-1}"
export RUNTIME_PROVIDER_TRACE="${RUNTIME_PROVIDER_TRACE:-1}"

FAULT="${STORAGE_MOCK_FAULT:-normal}"
if [ "$MODE" = "mock" ]; then
  unset STORAGE_MODEL_CONFIG || true
  export DASHSCOPE_BASE_URL="http://127.0.0.1:${MOCK_ROUTER_PORT}/v1"
  export DASHSCOPE_API_KEY="mock-key"
else
  if [ -z "${STORAGE_MODEL_CONFIG:-}" ] && [ -f "$PWD/config/model.local.yaml" ]; then
    export STORAGE_MODEL_CONFIG="$PWD/config/model.local.yaml"
  fi
  printf 'Real provider network owner: Unified Agent Runtime only\n'
fi

"$PYTHON_BIN" scripts/preflight.py --runtime-root "$RUNTIME_ROOT" --mode "$MODE"

export STORAGE_LIFE_DATA_DIR="${STORAGE_LIFE_DATA_DIR:-$PWD/.testdata/${MODE}-${FAULT}}"
export STORAGE_LIFE_RUNTIME_DB="${STORAGE_LIFE_RUNTIME_DB:-$PWD/.testdata/${MODE}-${FAULT}-runtime.sqlite3}"
if [ "${STORAGE_TEST_RESET_DATA:-1}" = "1" ]; then
  rm -rf "$STORAGE_LIFE_DATA_DIR"
  rm -f "$STORAGE_LIFE_RUNTIME_DB"
fi
mkdir -p "$STORAGE_LIFE_DATA_DIR"

PIDS=""
cleanup(){
  trap - EXIT INT TERM
  for p in $PIDS; do
    kill "$p" 2>/dev/null || true
  done
  for p in $PIDS; do
    wait "$p" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

wait_owned(){
  url="$1"; name="$2"; port="$3"; pid="$4"; log="$5"
  set +e
  "$PYTHON_BIN" scripts/port_guard.py wait-owned \
      --url "$url" --service "$name" --port "$port" --pid "$pid" \
      --timeout "${STORAGE_SERVICE_START_TIMEOUT:-90}" --log "$log"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  echo "PRODUCT_E2E=NOT_RUN" >&2
  exit "$rc"
}

if [ "$MODE" = "mock" ]; then
  "$PYTHON_BIN" -m tools.openai_mock.server --host 127.0.0.1 --port "$OPENAI_MOCK_PORT" >release/openai_mock.log 2>&1 &
  OPENAI_MOCK_PID=$!
  PIDS="$PIDS $OPENAI_MOCK_PID"
  wait_owned "http://127.0.0.1:${OPENAI_MOCK_PORT}/__mock__/health" "OpenAI-Mock" "$OPENAI_MOCK_PORT" "$OPENAI_MOCK_PID" "release/openai_mock.log"

  OPENAI_MOCK_UPSTREAM="http://127.0.0.1:${OPENAI_MOCK_PORT}" "$PYTHON_BIN" test_support/mock_router.py --port "$MOCK_ROUTER_PORT" >release/mock_router.log 2>&1 &
  MOCK_ROUTER_PID=$!
  PIDS="$PIDS $MOCK_ROUTER_PID"
  wait_owned "http://127.0.0.1:${MOCK_ROUTER_PORT}/health" "Storage-Mock-Router" "$MOCK_ROUTER_PORT" "$MOCK_ROUTER_PID" "release/mock_router.log"
fi

printf 'Storage Web protocol: plain HTTP (not HTTPS)\n'
printf 'Storage Web bind: %s:%s\n' "$WEB_HOST" "$WEB_PORT"
"$PYTHON_BIN" -m uvicorn storage_life.app:app --host "$WEB_HOST" --port "$WEB_PORT" >release/storage_app.log 2>&1 &
APP_PID=$!
PIDS="$PIDS $APP_PID"
if [ "${STORAGE_APP_LOG_STDOUT:-0}" = "1" ]; then
  tail -n 0 -F release/storage_app.log &
  LOG_TAIL_PID=$!
  PIDS="$PIDS $LOG_TAIL_PID"
  printf 'Agent/Provider HTTP trace is live. Look for [runtime-provider] lines.\n'
fi
wait_owned "$INTERNAL_BASE/api/health" "Storage-Web" "$WEB_PORT" "$APP_PID" "release/storage_app.log"

if [ "${STORAGE_MOCK_FAULT:-normal}" = "persistent_503" ]; then
  "$PYTHON_BIN" scripts/product_failure_e2e.py --base "$INTERNAL_BASE" --timeout "${STORAGE_PRODUCT_E2E_TIMEOUT:-180}"
else
  if [ "$MODE" = "real" ]; then
    E2E_TIMEOUT="${STORAGE_PRODUCT_E2E_TIMEOUT:-360}"
  else
    E2E_TIMEOUT="${STORAGE_PRODUCT_E2E_TIMEOUT:-120}"
  fi
  "$PYTHON_BIN" scripts/product_e2e.py --base "$INTERNAL_BASE" --timeout "$E2E_TIMEOUT"
fi

if [ -n "$PUBLIC_URL" ]; then
  DISPLAY_URL="$PUBLIC_URL"
elif [ "$WEB_HOST" = "0.0.0.0" ]; then
  DISPLAY_URL="http://<SERVER_IP>:$WEB_PORT"
else
  DISPLAY_URL="$INTERNAL_BASE"
fi
printf '\nPRODUCT E2E PASS.\nWeb: %s\nMode: %s\n' "$DISPLAY_URL" "$MODE"
printf 'IMPORTANT: this test package serves plain HTTP. Use http://, not https://.\n'
if [ "${STORAGE_TEST_NO_WAIT:-0}" = "1" ]; then
  exit 0
fi
printf 'Product test environment remains running. Press Ctrl+C to stop.\n'
wait "$APP_PID"
