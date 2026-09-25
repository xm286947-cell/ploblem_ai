#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ -n "${1:-}" ]; then LOG="$1"; elif [ -s release/storage_app.log ]; then LOG=release/storage_app.log; else LOG=logs/latest.log; fi
printf 'Watching Agent/Provider trace in %s
' "$LOG"
printf 'Ctrl+C to stop.
'
touch "$LOG"
tail -n 100 -F "$LOG" | grep --line-buffered -E 'PROVIDER_DIAGNOSTIC|PROVIDER_NETWORK_PROBE|\[runtime-provider\]|\[STORAGE_RUNTIME_HTTP\]|Invalid HTTP request|PROVIDER_|RuntimeBridge' || true
