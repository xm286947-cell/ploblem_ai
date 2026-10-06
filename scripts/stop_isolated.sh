#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

if [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  export PATH
fi

STATE_FILE="${PKR_ISOLATED_STATE_FILE:-$PWD/.pkr_isolated_last.env}"
if [ ! -f "$STATE_FILE" ]; then
  echo "ERROR: isolated state file not found; refusing to delete any Docker resources."
  exit 2
fi

. "$STATE_FILE"

case "${ISOLATED_PROJECT:-}" in storage-pkr-w4-*) ;; *) echo "ERROR: unsafe isolated project identity."; exit 2;; esac
case "${PKR_ISOLATED_CONTAINER:-}" in storage-public-knowledge-w4-*) ;; *) echo "ERROR: unsafe isolated container identity."; exit 2;; esac
case "${PKR_ISOLATED_VOLUME:-}" in storage_public_knowledge_w4_*_data) ;; *) echo "ERROR: unsafe isolated volume identity."; exit 2;; esac

export PKR_ISOLATED_CONTAINER PKR_ISOLATED_VOLUME PKR_ISOLATED_PORT

docker compose -p "$ISOLATED_PROJECT" -f compose.isolated.yaml down || true

if [ "${PKR_REMOVE_ISOLATED_VOLUME:-0}" = "1" ]; then
  docker volume rm -f "$PKR_ISOLATED_VOLUME" >/dev/null 2>&1 || true
  echo "ISOLATED_VOLUME_REMOVED=YES"
else
  echo "ISOLATED_VOLUME_REMOVED=NO"
fi

echo "Original Public Knowledge container/volume were not touched."
echo "Stopped isolated project: $ISOLATED_PROJECT"
