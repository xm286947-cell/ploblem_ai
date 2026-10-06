#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

if [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  export PATH
fi

ISOLATED_CONTAINER="storage-public-knowledge-w4-isolated"
ISOLATED_VOLUME="storage_public_knowledge_w4_isolated_data"
ISOLATED_PORT="19000"
SOURCE_VOLUME="${PKR_SOURCE_VOLUME:-}"

if [ -z "$SOURCE_VOLUME" ]; then
  if docker volume inspect public_knowledge_service_public_knowledge_data >/dev/null 2>&1; then
    SOURCE_VOLUME="public_knowledge_service_public_knowledge_data"
  else
    CANDIDATES="$(docker volume ls --format '{{.Name}}' | grep -E '(^|_)public_knowledge_data$' || true)"
    COUNT="$(printf '%s\n' "$CANDIDATES" | sed '/^$/d' | wc -l | tr -d ' ')"
    if [ "$COUNT" = "1" ]; then
      SOURCE_VOLUME="$(printf '%s\n' "$CANDIDATES" | sed '/^$/d')"
    else
      echo "ERROR: unable to select exactly one source Public Knowledge volume."
      echo "Set PKR_SOURCE_VOLUME to the existing volume name."
      exit 2
    fi
  fi
fi

docker volume inspect "$SOURCE_VOLUME" >/dev/null

# Only the isolated resources are ever replaced. The source container/volume
# are read-only inputs and are never stopped, removed, or modified.
docker rm -f "$ISOLATED_CONTAINER" >/dev/null 2>&1 || true
docker volume rm -f "$ISOLATED_VOLUME" >/dev/null 2>&1 || true
docker volume create "$ISOLATED_VOLUME" >/dev/null

docker run --rm \
  -v "$SOURCE_VOLUME:/from:ro" \
  -v "$ISOLATED_VOLUME:/to" \
  alpine:3.20 \
  sh -c 'cp -a /from/. /to/'

docker compose -f compose.isolated.yaml up -d --build --force-recreate --remove-orphans

echo "Waiting for isolated Public Knowledge: http://127.0.0.1:$ISOLATED_PORT"
python - "$ISOLATED_PORT" <<'PY'
import json, sys, time, urllib.request
port = sys.argv[1]
base = f"http://127.0.0.1:{port}"
last = None
for _ in range(60):
    try:
        with urllib.request.urlopen(base + "/health", timeout=3) as response:
            payload = json.load(response)
        if payload.get("status") == "ok":
            break
    except Exception as exc:
        last = exc
    time.sleep(1)
else:
    raise SystemExit(f"ISOLATED_PKR_HEALTH_FAILED:{last}")

spec = json.load(urllib.request.urlopen(base + "/openapi.json", timeout=5))
paths = spec.get("paths", {})
required = [
    "/providers/active/health",
    "/providers/openai-compatible/status",
    "/admin/effective-config",
]
missing = [item for item in required if item not in paths]
if missing:
    raise SystemExit("ISOLATED_PKR_CONTRACT_MISSING:" + ",".join(missing))

cfg = json.load(urllib.request.urlopen(base + "/config", timeout=5))
config = cfg.get("config") or {}
print("ISOLATED_PKR_HEALTH=PASS")
print("ISOLATED_PKR_ACTIVE_ROUTE=PASS")
print("ISOLATED_PKR_PROVIDER_TYPE=" + str(config.get("provider_type") or ""))
print("ISOLATED_PKR_MODEL=" + str(config.get("openai_model") or config.get("ollama_model") or ""))
PY

if [ "${PKR_ALLOW_REAL_PROVIDER_TEST:-0}" = "1" ]; then
  python - "$ISOLATED_PORT" <<'PY'
import json, sys, urllib.request, urllib.error
port = sys.argv[1]
url = f"http://127.0.0.1:{port}/providers/active/health"
try:
    with urllib.request.urlopen(url, timeout=150) as response:
        payload = json.load(response)
except urllib.error.HTTPError as exc:
    detail = exc.read().decode("utf-8", "replace")[:2000]
    raise SystemExit(f"ISOLATED_PKR_ACTIVE_PROVIDER_HEALTH_FAILED:{exc.code}:{detail}")
print("ISOLATED_PKR_ACTIVE_PROVIDER_HEALTH=PASS")
print("ISOLATED_PKR_ACTIVE_PROVIDER=" + str(payload.get("provider") or ""))
print("ISOLATED_PKR_ACTIVE_MODEL=" + str(payload.get("model") or ""))
PY
else
  echo "ISOLATED_PKR_REAL_PROVIDER_TEST=SKIPPED"
  echo "To run one real provider health probe: PKR_ALLOW_REAL_PROVIDER_TEST=1 bash scripts/start_isolated.sh"
fi

echo "Original Public Knowledge service was not stopped or modified."
echo "Isolated Public Knowledge: http://127.0.0.1:$ISOLATED_PORT"
