#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

MOCK_HOST="127.0.0.1"
MOCK_PORT="18090"
MODEL_CONFIG="$SCRIPT_DIR/config/runtime/model.w4-functional.yaml"

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

if [[ ! -f "$SCRIPT_DIR/validation/quality_scenario_w4_fixture.db" ]]; then
  echo "W4 fixture DB is missing."
  exit 3
fi

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

"$BASE_PYTHON" "$SCRIPT_DIR/tools/quality_scenario_functional_provider.py" --host "$MOCK_HOST" --port "$MOCK_PORT" || exit 4

echo "W4_MAC_FUNCTIONAL_PROVIDER=CONTROLLED_OPENAI_MOCK"
echo "REVERSE_QUALITY_MODEL_CONFIG=$REVERSE_QUALITY_MODEL_CONFIG"
echo "REAL_PROVIDER_GOLDEN=NO"

"$SCRIPT_DIR/start_w4_mac_functional_golden.command" "$@"
EXIT_CODE=$?
exit $EXIT_CODE
