#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

FIXTURE_DB="$SCRIPT_DIR/validation/quality_scenario_w4_fixture.db"
MODEL_CONFIG="$SCRIPT_DIR/config/runtime/model.w4-functional.yaml"
MOCK_HOST="127.0.0.1"
MOCK_PORT="18090"

if [[ ! -f "$FIXTURE_DB" ]]; then
  echo "W4 functional fixture database not found:"
  echo "  $FIXTURE_DB"
  exit 3
fi
if [[ ! -f "$MODEL_CONFIG" ]]; then
  echo "W4 functional provider config not found:"
  echo "  $MODEL_CONFIG"
  exit 4
fi

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

export LEGACY_QUALITY_ISSUE_DB_PATH="$FIXTURE_DB"
export QUALITY_SCENARIO_V1_DB_PATH="$FIXTURE_DB"
export W4_FUNCTIONAL_FIXTURE="1"
export W4_FUNCTIONAL_MOCK_API_KEY="w4-functional-test-key"
export REVERSE_QUALITY_MODEL_CONFIG="$MODEL_CONFIG"

MOCK_LOG="$SCRIPT_DIR/validation/w4_functional_provider.log"
"$BASE_PYTHON" "$SCRIPT_DIR/tools/openai_mock/server.py" --host "$MOCK_HOST" --port "$MOCK_PORT" >"$MOCK_LOG" 2>&1 &
MOCK_PID=$!

cleanup() {
  if kill -0 "$MOCK_PID" 2>/dev/null; then
    kill "$MOCK_PID" 2>/dev/null || true
    wait "$MOCK_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if ! "$BASE_PYTHON" "$SCRIPT_DIR/tools/quality_scenario_functional_provider.py" --host "$MOCK_HOST" --port "$MOCK_PORT"; then
  echo "W4_FUNCTIONAL_PROVIDER_START=FAIL"
  echo "Provider log:"
  tail -n 80 "$MOCK_LOG" 2>/dev/null || true
  exit 4
fi

echo "W4_MAC_FUNCTIONAL_FIXTURE=YES"
echo "FIXTURE_DB=$FIXTURE_DB"
echo "SYNTHETIC_BUSINESS_DATA=YES"
echo "DIRECT_QSV1_CANDIDATE_WRITE=NO"
echo "DIRECT_QSV1_PUBLISH_WRITE=NO"
echo "FUNCTIONAL_PROVIDER=CONTROLLED_OPENAI_MOCK"
echo "REVERSE_QUALITY_MODEL_CONFIG=$REVERSE_QUALITY_MODEL_CONFIG"
echo "REAL_PROVIDER_GOLDEN=NO"
echo "REAL_MATURE_DATA_GOLDEN=NO"

"$SCRIPT_DIR/start_quality_capability_p1.command" "$@"
EXIT_CODE=$?
exit $EXIT_CODE
