#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

if [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  export PATH
fi

if [ "$#" -gt 1 ]; then
  echo "Usage: bash scripts/stop_isolated.sh [instance_id]"
  exit 2
fi
if [ "$#" -eq 1 ]; then
  PKR_ISOLATED_INSTANCE_ID="$1"
  export PKR_ISOLATED_INSTANCE_ID
fi

STATE_DIR="$PWD/.pkr_isolated_instances"
if [ ! -d "$STATE_DIR" ]; then
  echo "ERROR: isolated state directory not found; refusing to delete any Docker resources."
  exit 2
fi

select_state_file() {
  if [ -n "${PKR_ISOLATED_INSTANCE_ID:-}" ]; then
    case "$PKR_ISOLATED_INSTANCE_ID" in
      *[!A-Za-z0-9_.-]*|"") echo "ERROR: unsafe isolated instance ID."; exit 2;;
    esac
    printf '%s/%s.env\n' "$STATE_DIR" "$PKR_ISOLATED_INSTANCE_ID"
    return
  fi

  RUNNING_FILES=""
  for file in "$STATE_DIR"/*.env; do
    [ -f "$file" ] || continue
    status="$(sed -n "s/^PKR_ISOLATED_STATUS='\([^']*\)'$/\1/p" "$file" | head -n1)"
    if [ "$status" = "RUNNING" ]; then
      RUNNING_FILES="${RUNNING_FILES}${RUNNING_FILES:+
}$file"
    fi
  done

  COUNT="$(printf '%s\n' "$RUNNING_FILES" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [ "$COUNT" = "1" ]; then
    printf '%s\n' "$RUNNING_FILES"
    return
  fi
  if [ "$COUNT" = "0" ]; then
    echo "ERROR: no running isolated instance state found." >&2
    exit 2
  fi

  echo "ERROR: multiple isolated instances are running; choose one explicitly." >&2
  echo "Run: bash scripts/list_isolated.sh" >&2
  echo "Then: PKR_ISOLATED_INSTANCE_ID=<id> bash scripts/stop_isolated.sh" >&2
  exit 2
}

stop_one() {
  STATE_FILE="$1"
  if [ ! -f "$STATE_FILE" ]; then
    echo "ERROR: isolated state file not found: $STATE_FILE"
    return 2
  fi

  . "$STATE_FILE"

  case "${PKR_ISOLATED_INSTANCE_ID:-}" in *[!A-Za-z0-9_.-]*|"") echo "ERROR: unsafe isolated instance identity."; return 2;; esac
  case "${ISOLATED_PROJECT:-}" in storage-pkr-w4-*) ;; *) echo "ERROR: unsafe isolated project identity."; return 2;; esac
  case "${PKR_ISOLATED_CONTAINER:-}" in storage-public-knowledge-w4-*) ;; *) echo "ERROR: unsafe isolated container identity."; return 2;; esac
  case "${PKR_ISOLATED_VOLUME:-}" in storage_public_knowledge_w4_*_data) ;; *) echo "ERROR: unsafe isolated volume identity."; return 2;; esac

  export PKR_ISOLATED_CONTAINER PKR_ISOLATED_VOLUME PKR_ISOLATED_PORT
  docker compose -p "$ISOLATED_PROJECT" -f compose.isolated.yaml down || true

  if [ "${PKR_REMOVE_ISOLATED_VOLUME:-0}" = "1" ]; then
    docker volume rm -f "$PKR_ISOLATED_VOLUME" >/dev/null 2>&1 || true
    VOLUME_STATUS="REMOVED"
  else
    VOLUME_STATUS="RETAINED"
  fi

  cat > "$STATE_FILE" <<EOF
PKR_ISOLATED_INSTANCE_ID='$PKR_ISOLATED_INSTANCE_ID'
ISOLATED_PROJECT='$ISOLATED_PROJECT'
PKR_ISOLATED_CONTAINER='$PKR_ISOLATED_CONTAINER'
PKR_ISOLATED_VOLUME='$PKR_ISOLATED_VOLUME'
PKR_ISOLATED_PORT='$PKR_ISOLATED_PORT'
PKR_SOURCE_VOLUME='${PKR_SOURCE_VOLUME:-}'
PKR_ISOLATED_STATUS='STOPPED'
PKR_ISOLATED_VOLUME_STATUS='$VOLUME_STATUS'
EOF

  echo "ISOLATED_INSTANCE_STOPPED=$PKR_ISOLATED_INSTANCE_ID"
  echo "ISOLATED_VOLUME_STATUS=$VOLUME_STATUS"
}

if [ "${PKR_STOP_ALL_ISOLATED:-0}" = "1" ]; then
  found=0
  for file in "$STATE_DIR"/*.env; do
    [ -f "$file" ] || continue
    status="$(sed -n "s/^PKR_ISOLATED_STATUS='\([^']*\)'$/\1/p" "$file" | head -n1)"
    if [ "$status" = "RUNNING" ]; then
      found=1
      stop_one "$file"
    fi
  done
  if [ "$found" = "0" ]; then
    echo "ERROR: no running isolated instances found."
    exit 2
  fi
else
  stop_one "$(select_state_file)"
fi

echo "Original Public Knowledge container/volume were not touched."
