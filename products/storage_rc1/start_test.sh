#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"
MODE="${1:-mock}"
mkdir -p logs release
STAMP="$(date +%Y%m%d_%H%M%S)"
SESSION_LOG="$PWD/logs/${MODE}_${STAMP}.log"
LATEST_LOG="$PWD/logs/latest.log"
: > "$SESSION_LOG"
ln -sfn "$(basename "$SESSION_LOG")" "$LATEST_LOG" 2>/dev/null || cp "$SESSION_LOG" "$LATEST_LOG"
: > release/storage_app.log
: > release/openai_mock.log
: > release/mock_router.log

if [ -f config/product_test.env ]; then
  set -a
  . ./config/product_test.env
  set +a
fi
export STORAGE_WEB_HOST="${STORAGE_WEB_HOST:-0.0.0.0}"
export STORAGE_WEB_PORT="${STORAGE_WEB_PORT:-8765}"
export RUNTIME_PROVIDER_TRACE="${RUNTIME_PROVIDER_TRACE:-1}"
export STORAGE_RUNTIME_HTTP_TRACE="${STORAGE_RUNTIME_HTTP_TRACE:-1}"
export STORAGE_APP_LOG_STDOUT="${STORAGE_APP_LOG_STDOUT:-1}"
if [ "$MODE" = "real" ] && [ -z "${STORAGE_MODEL_CONFIG:-}" ] && [ -f "$PWD/config/model.local.yaml" ]; then
  export STORAGE_MODEL_CONFIG="$PWD/config/model.local.yaml"
fi

printf 'SESSION_LOG=%s
' "$SESSION_LOG" | tee -a "$SESSION_LOG"
printf 'MODE=%s
' "$MODE" | tee -a "$SESSION_LOG"
printf 'STARTED_AT=%s
' "$(date -Is 2>/dev/null || date)" | tee -a "$SESSION_LOG"
set +e
bash ./run_server_test.sh "$MODE" "${UNIFIED_AGENT_RUNTIME_ROOT:-}" 2>&1 | tee -a "$SESSION_LOG"
status=${PIPESTATUS[0]}
set -e
if [ "$status" -ne 0 ]; then
  printf '
PRODUCT TEST FAILED exit=%s
' "$status" | tee -a "$SESSION_LOG"
  bash ./scripts/collect_failure.sh "$SESSION_LOG" || true
  printf 'Failure bundle: %s/logs/failure_latest.txt
' "$PWD" | tee -a "$SESSION_LOG"
else
  printf '
PRODUCT TEST PASS
' | tee -a "$SESSION_LOG"
fi
exit "$status"
