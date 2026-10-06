#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "[1/2] Starting repeat-safe isolated Public Knowledge"
bash "$ROOT/PUBLIC_KNOWLEDGE_SERVICE/scripts/start_isolated.sh"

STATE_FILE="$ROOT/PUBLIC_KNOWLEDGE_SERVICE/.pkr_isolated_last.env"
if [ ! -f "$STATE_FILE" ]; then
  echo "ERROR: isolated Public Knowledge state file missing."
  exit 2
fi

# shellcheck disable=SC1090
source "$STATE_FILE"

if [ -z "${PKR_ISOLATED_PORT:-}" ]; then
  echo "ERROR: isolated Public Knowledge port missing from state."
  exit 2
fi

echo "[2/2] Starting isolated Storage on 127.0.0.1:18765"
export PUBLIC_KNOWLEDGE_SERVICE_URL="http://127.0.0.1:$PKR_ISOLATED_PORT"
export PUBLIC_KNOWLEDGE_ALLOWED_URLS="http://127.0.0.1:$PKR_ISOLATED_PORT"
export STORAGE_WEB_HOST="127.0.0.1"
export STORAGE_WEB_PORT="18765"

echo "Existing Public Knowledge on 9000 is not modified."
echo "Isolated Public Knowledge: http://127.0.0.1:$PKR_ISOLATED_PORT"
echo "Isolated Storage: http://127.0.0.1:18765"
exec bash "$ROOT/run_macos.sh"
