#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Storage package Python environment is missing. Start the Storage product once to create it."
  exit 2
fi
exec "$PY" -m uvicorn knowledge_consumer_gateway.app:app \
  --host "${KNOWLEDGE_GATEWAY_BIND_HOST:-127.0.0.1}" \
  --port "${KNOWLEDGE_GATEWAY_PORT:-9001}"
