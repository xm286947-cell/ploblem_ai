#!/bin/zsh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VALIDATION_DIR="${QUALITY_SCENARIO_DEV_DIR:-$SCRIPT_DIR/validation/mac_dev_loop}"
SOURCE_DB="$VALIDATION_DIR/quality_scenario_source_fixture.db"
PY="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "DEV_LOOP_VENV_MISSING"
  exit 2
fi
if [[ ! -f "$SOURCE_DB" ]]; then
  echo "DEV_LOOP_SOURCE_FIXTURE_MISSING=$SOURCE_DB"
  exit 3
fi

"$PY" "$SCRIPT_DIR/tools/build_quality_scenario_test_fixture.py" --db "$SOURCE_DB" --advance-g5
echo "G5_SOURCE_REVISION_ADVANCED=PASS"
echo "ACTION=刷新 /software-assessment 后重新 Preview/Generate，验证新 lineage 且旧 PUBLISHED 历史保留"
