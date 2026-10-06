#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

STATE_DIR="$PWD/.pkr_isolated_instances"
if [ ! -d "$STATE_DIR" ]; then
  echo "NO_ISOLATED_INSTANCES"
  exit 0
fi

printf '%-28s %-10s %-7s %-44s %s\n' "INSTANCE_ID" "STATUS" "PORT" "CONTAINER" "VOLUME"
found=0
for file in "$STATE_DIR"/*.env; do
  [ -f "$file" ] || continue
  found=1
  instance="$(sed -n "s/^PKR_ISOLATED_INSTANCE_ID='\([^']*\)'$/\1/p" "$file" | head -n1)"
  status="$(sed -n "s/^PKR_ISOLATED_STATUS='\([^']*\)'$/\1/p" "$file" | head -n1)"
  port="$(sed -n "s/^PKR_ISOLATED_PORT='\([^']*\)'$/\1/p" "$file" | head -n1)"
  container="$(sed -n "s/^PKR_ISOLATED_CONTAINER='\([^']*\)'$/\1/p" "$file" | head -n1)"
  volume="$(sed -n "s/^PKR_ISOLATED_VOLUME='\([^']*\)'$/\1/p" "$file" | head -n1)"
  printf '%-28s %-10s %-7s %-44s %s\n' "$instance" "$status" "$port" "$container" "$volume"
done

if [ "$found" = "0" ]; then
  echo "NO_ISOLATED_INSTANCES"
fi
