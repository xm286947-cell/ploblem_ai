#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  export PATH
fi
docker compose up -d --build
echo "Public Knowledge Service: http://127.0.0.1:9000"
bash ./scripts/health.sh
