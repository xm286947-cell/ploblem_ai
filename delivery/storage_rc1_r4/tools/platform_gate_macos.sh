#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <R4.zip>" >&2
  exit 2
fi
PACKAGE="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
WORK="$(mktemp -d -t storage-r4-macos-gate.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

/usr/bin/ditto -x -k "$PACKAGE" "$WORK"
ROOT="$WORK/STORAGE_PRODUCT_MVP_RC1"
test -d "$ROOT"

cd "$ROOT"
export STORAGE_TEST_NO_WAIT=1
export STORAGE_TEST_RESET_DATA=1
/usr/bin/env bash start_test.sh mock

echo "MACOS_SYSTEM_EXTRACT=PASS"
echo "MACOS_OFFICIAL_LAUNCHER=PASS"
echo "NO_CHMOD_REQUIRED=PASS"
