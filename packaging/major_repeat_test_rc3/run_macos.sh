#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
EXPECTED_PLATFORM=darwin exec /bin/sh "$SCRIPT_DIR/run_posix.sh" "$@"
