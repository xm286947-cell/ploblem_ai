#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

if [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  export PATH
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "ERROR: python3/python is required for isolated validation checks."
    exit 2
  fi
fi

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

INSTANCE_ID="${PKR_ISOLATED_INSTANCE_ID:-$(date +%Y%m%d%H%M%S)-$$}"
case "$INSTANCE_ID" in
  *[!A-Za-z0-9_.-]*|"")
    echo "ERROR: PKR_ISOLATED_INSTANCE_ID may contain only letters, numbers, dot, dash, underscore."
    exit 2
    ;;
esac

ISOLATED_PROJECT="storage-pkr-w4-$INSTANCE_ID"
PKR_ISOLATED_CONTAINER="storage-public-knowledge-w4-$INSTANCE_ID"
PKR_ISOLATED_VOLUME="storage_public_knowledge_w4_${INSTANCE_ID}_data"

if [ -n "${PKR_ISOLATED_PORT:-}" ]; then
  PORT_CANDIDATE="$PKR_ISOLATED_PORT"
  "$PYTHON_BIN" - "$PORT_CANDIDATE" <<'PY'
import socket, sys
port = int(sys.argv[1])
if not 1024 <= port <= 65535:
    raise SystemExit("ISOLATED_PORT_INVALID")
with socket.socket() as sock:
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit("ISOLATED_PORT_IN_USE")
PY
else
  PKR_ISOLATED_PORT="$("$PYTHON_BIN" - <<'PY'
import socket
for port in range(19000, 19050):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        break
else:
    raise SystemExit("NO_FREE_ISOLATED_PORT")
PY
)"
fi

export PKR_ISOLATED_CONTAINER PKR_ISOLATED_VOLUME PKR_ISOLATED_PORT

if docker inspect "$PKR_ISOLATED_CONTAINER" >/dev/null 2>&1; then
  echo "ERROR: generated isolated container already exists; refusing to replace it."
  exit 2
fi
if docker volume inspect "$PKR_ISOLATED_VOLUME" >/dev/null 2>&1; then
  echo "ERROR: generated isolated volume already exists; refusing to replace it."
  exit 2
fi

STATE_FILE="$PWD/.pkr_isolated_last.env"
umask 077
cat > "$STATE_FILE" <<EOF
ISOLATED_PROJECT='$ISOLATED_PROJECT'
PKR_ISOLATED_CONTAINER='$PKR_ISOLATED_CONTAINER'
PKR_ISOLATED_VOLUME='$PKR_ISOLATED_VOLUME'
PKR_ISOLATED_PORT='$PKR_ISOLATED_PORT'
PKR_SOURCE_VOLUME='$SOURCE_VOLUME'
EOF

cleanup_on_error() {
  code=$?
  if [ "$code" -ne 0 ]; then
    echo "Isolated startup failed; cleaning only resources created by this run."
    docker compose -p "$ISOLATED_PROJECT" -f compose.isolated.yaml down >/dev/null 2>&1 || true
    docker volume rm -f "$PKR_ISOLATED_VOLUME" >/dev/null 2>&1 || true
  fi
  exit "$code"
}
trap cleanup_on_error EXIT HUP INT TERM

docker volume create "$PKR_ISOLATED_VOLUME" >/dev/null

docker run --rm   -v "$SOURCE_VOLUME:/from:ro"   -v "$PKR_ISOLATED_VOLUME:/to"   alpine:3.20   sh -c 'cp -a /from/. /to/'

docker compose -p "$ISOLATED_PROJECT" -f compose.isolated.yaml up -d --build

echo "Waiting for isolated Public Knowledge: http://127.0.0.1:$PKR_ISOLATED_PORT"
"$PYTHON_BIN" - "$PKR_ISOLATED_PORT" <<'PY'
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
  "$PYTHON_BIN" - "$PKR_ISOLATED_PORT" <<'PY'
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
fi

trap - EXIT HUP INT TERM

echo "Original Public Knowledge service was not stopped or modified."
echo "ISOLATED_STATE_FILE=$STATE_FILE"
echo "ISOLATED_PROJECT=$ISOLATED_PROJECT"
echo "ISOLATED_CONTAINER=$PKR_ISOLATED_CONTAINER"
echo "ISOLATED_VOLUME=$PKR_ISOLATED_VOLUME"
echo "Isolated Public Knowledge: http://127.0.0.1:$PKR_ISOLATED_PORT"
