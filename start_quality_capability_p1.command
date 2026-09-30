#!/bin/zsh
# Mature Quality Issue host + additive QualityScenario V1 preview.
# Keep LF line endings; see .gitattributes.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "Starting Quality Issue Analysis Engine with QualityScenario V1 preview..."
echo "Open http://127.0.0.1:8080/issues after startup."

QUALITY_DB="${LEGACY_QUALITY_ISSUE_DB_PATH:-knowledge/quality_issue_v1.db}"
echo "Mature host DB: $QUALITY_DB"
if [[ -n "${QUALITY_SCENARIO_V1_DB_PATH:-}" ]]; then
  echo "QualityScenario V1 DB: $QUALITY_SCENARIO_V1_DB_PATH"
else
  echo "QualityScenario V1 DB: same as mature host DB"
fi

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
    echo "Python >= 3.11 was not found."
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
  "$PYTHON_CMD" -m pip install --upgrade pip || exit 2
  "$PYTHON_CMD" -m pip install -r requirements.txt -r requirements-runtime-p0-test.txt || exit 2
fi

"$PYTHON_CMD" main.py knowledge-web --db "$QUALITY_DB" "$@"
EXIT_CODE=$?
echo
if [[ -t 0 ]]; then
  read -r "?Press Return to close this window..."
fi
exit $EXIT_CODE
