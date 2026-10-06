#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  export PATH
fi

docker compose -p storage-pkr-w4-isolated -f compose.isolated.yaml down || true
docker rm -f storage-public-knowledge-w4-isolated >/dev/null 2>&1 || true

if [ "${PKR_REMOVE_ISOLATED_VOLUME:-0}" = "1" ]; then
  docker volume rm -f storage_public_knowledge_w4_isolated_data >/dev/null 2>&1 || true
  echo "ISOLATED_VOLUME_REMOVED=YES"
else
  echo "ISOLATED_VOLUME_REMOVED=NO"
fi

echo "Original Public Knowledge container/volume were not touched."
