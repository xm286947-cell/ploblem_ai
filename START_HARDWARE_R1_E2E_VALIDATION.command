#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
exec "$PYTHON" scripts/hardware_r1_e2e_validation_start.py "$@"
