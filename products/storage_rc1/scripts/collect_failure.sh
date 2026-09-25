#!/bin/sh
set -u
cd "$(dirname "$0")/.."
SESSION_LOG="${1:-logs/latest.log}"
mkdir -p logs release
OUT="logs/failure_$(date +%Y%m%d_%H%M%S).txt"
{
  echo "STORAGE PRODUCT TEST FAILURE BUNDLE"
  echo "generated_at=$(date -Is 2>/dev/null || date)"
  echo "session_log=$SESSION_LOG"
  echo
  for f in "$SESSION_LOG" release/storage_app.log release/openai_mock.log release/mock_router.log release/PRODUCT_E2E_RESULT.json release/PRODUCT_FAILURE_E2E_RESULT.json; do
    echo "===== $f ====="
    if [ -f "$f" ]; then tail -n 400 "$f"; else echo "<not created>"; fi
    echo
  done
} > "$OUT" 2>&1
cp "$OUT" logs/failure_latest.txt
printf '%s
' "$OUT"
