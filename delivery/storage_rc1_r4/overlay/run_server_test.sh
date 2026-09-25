#!/bin/sh
set -eu
cd "$(dirname "$0")"
export STORAGE_WEB_HOST="${STORAGE_WEB_HOST:-0.0.0.0}"
export STORAGE_WEB_PORT="${STORAGE_WEB_PORT:-8765}"
export STORAGE_RUNTIME_HTTP_TRACE="${STORAGE_RUNTIME_HTTP_TRACE:-1}"
export STORAGE_APP_LOG_STDOUT="${STORAGE_APP_LOG_STDOUT:-1}"
printf 'Server test mode: Web will listen on %s:%s using plain HTTP.\n' "$STORAGE_WEB_HOST" "$STORAGE_WEB_PORT"
printf 'Open from client with: http://<SERVER_IP>:%s  (NOT https://)\n' "$STORAGE_WEB_PORT"
exec bash ./run_product_test.sh "${1:-mock}" "${2:-}"
