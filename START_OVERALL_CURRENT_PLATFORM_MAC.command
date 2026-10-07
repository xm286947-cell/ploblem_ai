#!/bin/zsh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

LEGACY_FIXTURE_DB="$SCRIPT_DIR/validation/quality_scenario_w4_fixture.db"
P0_DB="$SCRIPT_DIR/validation/overall_current_platform_p0.db"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -f "$LEGACY_FIXTURE_DB" ]]; then
  echo "OVERALL_TEST_FIXTURE_MISSING=$LEGACY_FIXTURE_DB"
  exit 3
fi

export LEGACY_QUALITY_ISSUE_DB_PATH="$LEGACY_FIXTURE_DB"
export PYTHONPATH="$SCRIPT_DIR"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

echo "PACKAGE_MODE=OVERALL_CURRENT_PLATFORM"
echo "DEFAULT_ENTRY=http://127.0.0.1:18080/p0/overall"
echo "LEGACY_QUALITY_ISSUE_DB_PATH=$LEGACY_QUALITY_ISSUE_DB_PATH"
echo "P0_DB=$P0_DB"
echo "QS_W4_SYNTHETIC_SOURCE_FIXTURE=YES"
echo "QS_W4_FIXTURE_IS_SUPPORTING_TEST_DATA=YES"
echo "DIRECT_QSV1_CANDIDATE_WRITE=NO"
echo "DIRECT_QSV1_PUBLISH_WRITE=NO"

if [[ ! -x "$VENV_PYTHON" ]]; then
  BASE_PYTHON=""
  for candidate in python3.12 python3.11 python3; do
    if (( $+commands[$candidate] )); then
      BASE_PYTHON="$commands[$candidate]"
      break
    fi
  done
  if [[ -z "$BASE_PYTHON" ]]; then
    echo "Python >= 3.11 was not found."
    exit 2
  fi
  "$BASE_PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"Python >= 3.11 required, found {sys.version.split()[0]}")
PY
  "$BASE_PYTHON" -m venv "$SCRIPT_DIR/.venv"
fi

if ! "$VENV_PYTHON" - <<'PY'
import fastapi, openpyxl, uvicorn
print("OVERALL_DEPENDENCY_IMPORT=PASS")
PY
then
  "$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt" -r "$SCRIPT_DIR/requirements-runtime-p0-test.txt"
fi

exec "$VENV_PYTHON" "$SCRIPT_DIR/main.py" knowledge-p1-start --db "$P0_DB" "$@"
