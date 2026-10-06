#!/bin/sh
set -eu
cd "$(dirname "$0")"

if [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  export PATH
fi

# The validation packages intentionally reuse the same local container name and
# persistent volume. Remove only the old service container so a stale image can
# never keep serving an older API contract. The named data volume is preserved.
docker rm -f storage-public-knowledge >/dev/null 2>&1 || true

docker compose up -d --build --force-recreate --remove-orphans

echo "Public Knowledge Service: http://127.0.0.1:9000"
bash ./scripts/health.sh

# A healthy process is not enough for Storage maintenance flows. Verify the
# running container exposes the source-deletion contract required by the UI.
docker exec storage-public-knowledge python -c '
import json, urllib.request
spec=json.load(urllib.request.urlopen("http://127.0.0.1:8080/openapi.json", timeout=5))
paths=spec.get("paths", {})
required=[
    "/sources/{source_id}",
    "/sources/{source_id}/revisions/{revision_id}",
]
missing=[p for p in required if "delete" not in paths.get(p, {})]
if missing:
    raise SystemExit("PUBLIC_KNOWLEDGE_DELETE_CONTRACT_MISSING:"+",".join(missing))
print("PUBLIC_KNOWLEDGE_DELETE_CONTRACT=PASS")
'
