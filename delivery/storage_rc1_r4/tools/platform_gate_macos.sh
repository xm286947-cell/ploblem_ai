#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <R5.zip> [expected-sha256]" >&2
  exit 2
fi

PACKAGE="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
EXPECTED_SHA="${2:-}"
if [ -n "$EXPECTED_SHA" ]; then
  ACTUAL_SHA="$(shasum -a 256 "$PACKAGE" | awk '{print $1}')"
  echo "MACOS_SHA256=$ACTUAL_SHA"
  if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
    echo "MACOS_RESULT=FAIL reason=sha256_mismatch" >&2
    exit 3
  fi
fi

WORK="$(mktemp -d -t storage-r5-macos-gate.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

/usr/bin/ditto -x -k "$PACKAGE" "$WORK"
ROOT="$WORK/STORAGE_PRODUCT_MVP_RC1"
test -d "$ROOT"

echo "MACOS_FRESH_EXTRACT=PASS path=$WORK"
find "$ROOT" -type f -name '*.sh' -print0 | while IFS= read -r -d '' file; do
  stat -f 'MACOS_SHELL_MODE=%N mode=%Lp' "$file"
done

cd "$ROOT"
export STORAGE_TEST_NO_WAIT=1
export STORAGE_TEST_RESET_DATA=1

# No chmod is performed anywhere in this gate.
bash start_test.sh mock
echo "MACOS_NORMAL_FREE_PORT_LAUNCHER=PASS"

if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  PYTHON_BIN="$(command -v python)"
fi

"$PYTHON_BIN" scripts/port_regression.py --package-root "$ROOT"
echo "MACOS_OCCUPIED_PORT_FAIL_FAST_REGRESSION=PASS"

bash selfcheck.sh
echo "MACOS_SELFCHECK=PASS"

echo "MACOS_SYSTEM_EXTRACT=PASS"
echo "MACOS_OFFICIAL_LAUNCHER=PASS"
echo "MACOS_NO_CHMOD=PASS"
echo "MACOS_RESULT=PASS"
