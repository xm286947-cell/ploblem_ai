#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

FIXTURE_DB="$SCRIPT_DIR/validation/quality_scenario_w4_fixture.db"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  BASE_PYTHON=""
  for candidate in python3.12 python3.11 python3; do
    if (( $+commands[$candidate] )); then
      BASE_PYTHON="$commands[$candidate]"
      break
    fi
  done
  VENV_PYTHON="$BASE_PYTHON"
fi

if [[ -z "$VENV_PYTHON" || ! -f "$FIXTURE_DB" ]]; then
  echo "W4_G5_FIXTURE_NOT_READY"
  exit 3
fi

"$VENV_PYTHON" "$SCRIPT_DIR/tools/build_quality_scenario_test_fixture.py"   --db "$FIXTURE_DB" --advance-g5 || exit 4

"$VENV_PYTHON" - "$FIXTURE_DB.fixture.json" <<'PY'
import json, sys
p=sys.argv[1]
m=json.load(open(p,encoding="utf-8"))
g5=next(x for x in m["cases"] if x["case_id"]=="G5_SOURCE_REVISION")
print("W4_G5_SOURCE_REVISION=ADVANCED")
print("G5_SOFTWARE_ASSESSMENT_MATERIAL_ID="+g5["software_assessment_material_id"])
print("G5_RESOLUTION_REVISION="+str(m.get("g5_resolution_revision")))
print("G5_REVISION_MATERIAL_ID="+str(m.get("g5_revision_material_id") or ""))
PY
