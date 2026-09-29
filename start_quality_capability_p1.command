#!/bin/zsh
# Double-click-friendly macOS launcher for the legacy quality capability.
# Keep this as the macOS equivalent of start_quality_capability_p1.bat.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "Starting Quality Issue Analysis Engine (integrated stable UI)..."
echo "Open http://127.0.0.1:8080/issues after startup."

QUALITY_DB="${LEGACY_QUALITY_ISSUE_DB_PATH:-knowledge/quality_issue_v1.db}"
echo "Legacy DB: $QUALITY_DB"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_CMD=(".venv/bin/python")
elif (( $+commands[python3.11] )); then
  PYTHON_CMD=("$commands[python3.11]")
else
  echo "Python 3.11 was not found. Create .venv or install python3.11."
  exit 2
fi

"${PYTHON_CMD[@]}" main.py knowledge-web --db "$QUALITY_DB" "$@"
EXIT_CODE=$?
echo
if [[ -t 0 ]]; then
  read -r "?Press Return to close this window..."
fi
exit $EXIT_CODE
