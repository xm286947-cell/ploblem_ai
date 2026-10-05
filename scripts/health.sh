#!/bin/sh
set -eu
if [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  export PATH
fi
# Operator-facing host port. The service listens on 8080 inside the container.
base="${PKR_BASE_URL:-http://127.0.0.1:9000}"
for attempt in $(seq 1 30); do
  if curl --fail --silent "$base/health"; then
    printf '\n'
    exit 0
  fi
  sleep 1
done
echo "Public Knowledge Service did not become healthy within 30 seconds." >&2
exit 1
