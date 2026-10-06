#!/bin/zsh
# Mature Quality Issue host + additive QualityScenario V1 preview.
# Keep LF line endings; see .gitattributes.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "Starting Quality Issue Analysis Engine with QualityScenario V1 preview..."
echo "PACKAGE_ROOT=$SCRIPT_DIR"
echo "Open http://127.0.0.1:8080/issues after startup."

QUALITY_DB="${LEGACY_QUALITY_ISSUE_DB_PATH:-$SCRIPT_DIR/knowledge/quality_issue_v1.db}"
if [[ -n "${QUALITY_SCENARIO_V1_DB_PATH:-}" ]]; then
  echo "QualityScenario V1 DB: $QUALITY_SCENARIO_V1_DB_PATH"
else
  echo "QualityScenario V1 DB: same as mature host DB"
fi

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Package-local .venv not found. Creating it now..."
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
  [[ $? -eq 0 ]] || exit 2
  "$BASE_PYTHON" -m venv "$SCRIPT_DIR/.venv" || exit 2
fi

echo "VENV_PYTHON=$VENV_PYTHON"
"$VENV_PYTHON" - <<'PY'
import sys
print(f"PYTHON_EXECUTABLE={sys.executable}")
print(f"PYTHON_PREFIX={sys.prefix}")
PY
[[ $? -eq 0 ]] || exit 2

if ! "$VENV_PYTHON" - <<'PY'
import openpyxl
print(f"OPENPYXL_VERSION={openpyxl.__version__}")
print(f"OPENPYXL_FILE={openpyxl.__file__}")
PY
then
  echo "OPENPYXL_IMPORT=FAIL"
  echo "The package-local venv is incomplete. Installing declared product dependencies..."
  "$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt" -r "$SCRIPT_DIR/requirements-runtime-p0-test.txt" || exit 2
  "$VENV_PYTHON" - <<'PY'
import openpyxl
print(f"OPENPYXL_VERSION={openpyxl.__version__}")
print(f"OPENPYXL_FILE={openpyxl.__file__}")
PY
  [[ $? -eq 0 ]] || exit 2
fi

echo "DEPENDENCY_PREFLIGHT=PASS"
"$VENV_PYTHON" "$SCRIPT_DIR/tools/quality_scenario_real_data_preflight.py" --db "$QUALITY_DB"
echo "For Browser Golden, require real data with:"
echo "  $VENV_PYTHON tools/quality_scenario_real_data_preflight.py --db \"$QUALITY_DB\" --require"
"$VENV_PYTHON" "$SCRIPT_DIR/main.py" knowledge-web --db "$QUALITY_DB" "$@"
EXIT_CODE=$?
echo
if [[ -t 0 ]]; then
  read -r "?Press Return to close this window..."
fi
exit $EXIT_CODE
