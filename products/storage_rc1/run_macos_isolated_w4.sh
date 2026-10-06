#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "[1/2] Starting isolated Public Knowledge on 127.0.0.1:19000"
bash "$ROOT/PUBLIC_KNOWLEDGE_SERVICE/scripts/start_isolated.sh"

echo "[2/2] Starting isolated Storage on 127.0.0.1:18765"
export PUBLIC_KNOWLEDGE_SERVICE_URL="http://127.0.0.1:19000"
export PUBLIC_KNOWLEDGE_ALLOWED_URLS="http://127.0.0.1:19000"
export STORAGE_WEB_HOST="127.0.0.1"
export STORAGE_WEB_PORT="18765"

echo "Existing Public Knowledge on 9000 is not modified."
echo "Isolated Public Knowledge: http://127.0.0.1:19000"
echo "Isolated Storage: http://127.0.0.1:18765"
exec bash "$ROOT/run_macos.sh"
