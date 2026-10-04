#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  export PATH
fi
name=pkr-fail-closed-test
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
docker run -d --rm --name "$name" -p 127.0.0.1:18081:8080 \
  -e PKR_DATA_DIR=/tmp/pkr-fail-closed \
  -e OLLAMA_URL=http://127.0.0.1:1 \
  -e OLLAMA_TIMEOUT_SECONDS=1 \
  -e OLLAMA_MODEL=qwen3-vl:8b-thinking-q4_K_M \
  -e OLLAMA_MODEL_DIGEST=901cae73216286ea8c5aba8b46d307ff7188f737285ec500c795a12f05225d28 \
  storage-public-knowledge:0.1.0 >/dev/null
for attempt in $(seq 1 20); do
  if curl --fail --silent http://127.0.0.1:18081/health >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent --show-error -H 'Content-Type: application/json' \
  -d '{"title":"Synthetic public fail-closed probe","classification":"PUBLIC","content":"A public test note has a generic smoke marker."}' \
  http://127.0.0.1:18081/sources/import >/dev/null
code=$(curl --silent --output evidence/fail-closed-response.json --write-out '%{http_code}' \
  -H 'Content-Type: application/json' -d '{"question":"What does the public test note say?"}' \
  http://127.0.0.1:18081/ask)
test "$code" = 503
python3 -c 'import json; d=json.load(open("evidence/fail-closed-response.json")); assert "failed closed" in d["detail"]'
printf '{"result":"PASS","http_status":%s,"provider":"unreachable-test-endpoint","answer_returned":false}\n' "$code" | tee evidence/fail-closed-summary.json
