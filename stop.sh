#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  export PATH
fi
docker compose down
