#!/bin/zsh
# Double-click-friendly macOS launcher for the mature legacy quality capability.
# This launcher must stay LF-only; see .gitattributes.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "Starting Quality Issue Analysis Engine (integrated stable UI)..."
echo "Open http://127.0.0.1:8080/issues after startup."

QUALITY_DB="${LEGACY_QUALITY_ISSUE_DB_PATH:-knowledge/quality_issue_v1.db}"

if [[ ! -f "$QUALITY_DB" ]]; then
  echo "MATURE_DB_BOUND=FAIL"
  echo "Legacy quality DB was not found: $QUALITY_DB"
  echo "Set LEGACY_QUALITY_ISSUE_DB_PATH to the existing mature quality_issue_v1.db."
  exit 3
fi

QUALITY_DB_ABS="$(cd "$(dirname "$QUALITY_DB")" && pwd)/$(basename "$QUALITY_DB")"
echo "Legacy DB: $QUALITY_DB_ABS"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_CMD=".venv/bin/python"
else
  BASE_PYTHON=""
  for candidate in python3.12 python3.11 python3; do
    if (( $+commands[$candidate] )); then
      BASE_PYTHON="$commands[$candidate]"
      break
    fi
  done

  if [[ -z "$BASE_PYTHON" ]]; then
    echo "No compatible Python was found. Python 3.12 or 3.11 is recommended."
    exit 2
  fi

  "$BASE_PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"Python >= 3.11 required, found {sys.version.split()[0]}")
print(f"BOOTSTRAP_PYTHON={sys.version.split()[0]}")
PY
  if [[ $? -ne 0 ]]; then
    exit 2
  fi

  echo "Creating isolated .venv..."
  "$BASE_PYTHON" -m venv .venv || exit 2
  PYTHON_CMD=".venv/bin/python"

  echo "Installing product dependencies into .venv..."
  "$PYTHON_CMD" -m pip install --upgrade pip || exit 2
  "$PYTHON_CMD" -m pip install -r requirements.txt -r requirements-runtime-p0-test.txt || exit 2
fi

"$PYTHON_CMD" tools/verify_legacy_quality_db.py "$QUALITY_DB_ABS"
DB_CHECK=$?
if [[ $DB_CHECK -ne 0 ]]; then
  echo "STEP1 startup blocked: mature Quality Scenario / Portrait data is not available in the bound DB."
  exit $DB_CHECK
fi

"$PYTHON_CMD" main.py knowledge-web --db "$QUALITY_DB_ABS" "$@"
EXIT_CODE=$?
echo
if [[ -t 0 ]]; then
  read -r "?Press Return to close this window..."
fi
exit $EXIT_CODE
