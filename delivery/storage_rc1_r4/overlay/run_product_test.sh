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
INTERNAL_BASE="http://127.0.0.1:${WEB_PORT}"
PUBLIC_URL="${STORAGE_PUBLIC_URL:-}"
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
  export DASHSCOPE_BASE_URL="http://127.0.0.1:18001/v1"
  export DASHSCOPE_API_KEY="mock-key"
elif [ "$MODE" = "real" ]; then
  if [ -z "${STORAGE_MODEL_CONFIG:-}" ] && [ -f "$PWD/config/model.local.yaml" ]; then
    export STORAGE_MODEL_CONFIG="$PWD/config/model.local.yaml"
  fi
else
  echo "ERROR mode must be mock or real" >&2
  exit 2
fi

if [ "$MODE" = "real" ]; then
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
cleanup(){ for p in $PIDS; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM
wait_url(){
  url="$1"; name="$2"; n="${3:-80}"
  i=1
  while [ "$i" -le "$n" ]; do
    if "$PYTHON_BIN" - "$url" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=1).read()
PY
    then return 0; fi
    sleep .25; i=$((i+1))
  done
  echo "ERROR $name health check failed: $url" >&2
  return 1
}
if [ "$MODE" = "mock" ]; then
  "$PYTHON_BIN" -m tools.openai_mock.server --host 127.0.0.1 --port 18000 >release/openai_mock.log 2>&1 & PIDS="$PIDS $!"
  wait_url http://127.0.0.1:18000/__mock__/health OPENAI-MOCK
  OPENAI_MOCK_UPSTREAM=http://127.0.0.1:18000 "$PYTHON_BIN" test_support/mock_router.py --port 18001 >release/mock_router.log 2>&1 & PIDS="$PIDS $!"
  wait_url http://127.0.0.1:18001/health Storage-Mock-Router
fi
printf 'Storage Web protocol: plain HTTP (not HTTPS)\n'
printf 'Storage Web bind: %s:%s\n' "$WEB_HOST" "$WEB_PORT"
"$PYTHON_BIN" -m uvicorn storage_life.app:app --host "$WEB_HOST" --port "$WEB_PORT" >release/storage_app.log 2>&1 & APP_PID=$!; PIDS="$PIDS $APP_PID"
if [ "${STORAGE_APP_LOG_STDOUT:-0}" = "1" ]; then
  tail -n 0 -F release/storage_app.log & LOG_TAIL_PID=$!; PIDS="$PIDS $LOG_TAIL_PID"
  printf 'Agent/Provider HTTP trace is live. Look for [runtime-provider] lines.\n'
fi
wait_url "$INTERNAL_BASE/api/health" Storage-Web
if [ "${STORAGE_MOCK_FAULT:-normal}" = "persistent_503" ]; then
  "$PYTHON_BIN" scripts/product_failure_e2e.py --base "$INTERNAL_BASE" --timeout "${STORAGE_PRODUCT_E2E_TIMEOUT:-180}"
else
  if [ "$MODE" = "real" ]; then E2E_TIMEOUT="${STORAGE_PRODUCT_E2E_TIMEOUT:-360}"; else E2E_TIMEOUT="${STORAGE_PRODUCT_E2E_TIMEOUT:-120}"; fi
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
