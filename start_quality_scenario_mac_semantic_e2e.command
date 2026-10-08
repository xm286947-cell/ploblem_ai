#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PY="$SCRIPT_DIR/.venv/bin/python"
LOCAL_MODEL="$SCRIPT_DIR/config/runtime/model.local.yaml"
SHARED_MODEL="$SCRIPT_DIR/config/runtime/model.yaml"
EVIDENCE_DIR="${QS_SEMANTIC_EVIDENCE_DIR:-$SCRIPT_DIR/validation/mac_semantic_e2e}"

echo "TASK=QUALITY_SCENARIO_MAC_SEMANTIC_E2E_001"
echo "PLATFORM=macOS"
echo "MODE=REAL_PROVIDER_SEMANTIC_ACCEPTANCE"
echo "SOURCE_DATA=CONTROLLED_G1_G5_SOURCE_FACTS"
echo "DIRECT_QSV1_CANDIDATE_WRITE=NO"
echo "DIRECT_QSV1_PUBLISH_WRITE=NO"

if [[ ! -x "$PY" ]]; then
  BASE_PYTHON=""
  for candidate in python3.12 python3.11 python3; do
    if (( $+commands[$candidate] )); then
      BASE_PYTHON="$commands[$candidate]"
      break
    fi
  done
  if [[ -z "$BASE_PYTHON" ]]; then
    echo "PYTHON_NOT_FOUND"
    exit 2
  fi
  "$BASE_PYTHON" -m venv "$SCRIPT_DIR/.venv"
  "$SCRIPT_DIR/.venv/bin/python" -m pip install -r requirements.txt -r requirements-runtime-p0-test.txt
  "$SCRIPT_DIR/.venv/bin/python" -m pip install playwright
  "$SCRIPT_DIR/.venv/bin/python" -m playwright install chromium
fi

if [[ -n "${QS_SEMANTIC_MODEL_CONFIG:-}" ]]; then
  MODEL_CONFIG="$QS_SEMANTIC_MODEL_CONFIG"
elif [[ -f "$LOCAL_MODEL" ]]; then
  MODEL_CONFIG="$LOCAL_MODEL"
else
  MODEL_CONFIG="$SHARED_MODEL"
fi

if [[ ! -f "$MODEL_CONFIG" ]]; then
  echo "SEMANTIC_MODEL_CONFIG_NOT_FOUND=$MODEL_CONFIG"
  exit 3
fi

export REVERSE_QUALITY_MODEL_CONFIG="$MODEL_CONFIG"
export QS_SEMANTIC_EVIDENCE_DIR="$EVIDENCE_DIR"

echo "MODEL_CONFIG_SOURCE=$(basename "$MODEL_CONFIG")"
echo "CREDENTIAL_VALUES_PRINTED=NO"

"$PY" "$SCRIPT_DIR/tools/run_quality_scenario_mac_semantic_e2e.py"
RESULT=$?

echo "SEMANTIC_REPORT=$EVIDENCE_DIR/SEMANTIC_ACCEPTANCE_REPORT.md"
echo "SEMANTIC_JSON=$EVIDENCE_DIR/semantic_e2e_result.json"

if (( $+commands[open] )) && [[ -f "$EVIDENCE_DIR/SEMANTIC_ACCEPTANCE_REPORT.md" ]]; then
  open "$EVIDENCE_DIR/SEMANTIC_ACCEPTANCE_REPORT.md" || true
fi

exit $RESULT
