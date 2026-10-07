#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_HOST="127.0.0.1"
APP_PORT="${QUALITY_SCENARIO_DEV_PORT:-18080}"
MOCK_HOST="127.0.0.1"
MOCK_PORT="18090"
VALIDATION_DIR="${QUALITY_SCENARIO_DEV_DIR:-$SCRIPT_DIR/validation/mac_dev_loop}"
SOURCE_DB="$VALIDATION_DIR/quality_scenario_source_fixture.db"
QSV1_DB="$VALIDATION_DIR/quality_scenario_v1_dev.db"
APP_LOG="$VALIDATION_DIR/mature_app.log"
MOCK_LOG="$VALIDATION_DIR/provider_mock.log"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
APP_PID=""
MOCK_PID=""

cleanup() {
  local code=$?
  if [[ -n "$APP_PID" ]]; then
    kill "$APP_PID" >/dev/null 2>&1 || true
    wait "$APP_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$MOCK_PID" ]]; then
    kill "$MOCK_PID" >/dev/null 2>&1 || true
    wait "$MOCK_PID" >/dev/null 2>&1 || true
  fi
  exit $code
}
trap cleanup EXIT INT TERM

echo "TASK=QUALITY_SCENARIO_MAC_DEV_TEST_LOOP"
echo "MODE=TEST_ONLY_CONTROLLED_DATA"
echo "PLATFORM_BASELINE=macOS"
echo "MATURE_RUNTIME_ROOT=quality_knowledge.web.app.create_app"
echo "PRODUCT_ENTRY=/software-assessment#quality-scenario-production"
echo "DIRECT_QSV1_CANDIDATE_WRITE=NO"
echo "DIRECT_QSV1_PUBLISH_WRITE=NO"
echo "WINDOWS_GATE=DEFERRED_TO_HUMAN_COMPATIBILITY_ACCEPTANCE"

mkdir -p "$VALIDATION_DIR"

if [[ ! -x "$VENV_PYTHON" ]]; then
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
fi

if ! "$VENV_PYTHON" - <<'PY'
import fastapi, openpyxl, yaml
print("DEV_LOOP_DEPENDENCIES=READY")
PY
then
  "$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt" -r "$SCRIPT_DIR/requirements-runtime-p0-test.txt"
fi

if [[ -f "$SOURCE_DB" ]]; then
  "$VENV_PYTHON" "$SCRIPT_DIR/tools/build_quality_scenario_test_fixture.py" --db "$SOURCE_DB" --reset
else
  "$VENV_PYTHON" "$SCRIPT_DIR/tools/build_quality_scenario_test_fixture.py" --db "$SOURCE_DB"
fi

rm -f "$QSV1_DB" "$QSV1_DB-wal" "$QSV1_DB-shm"

"$VENV_PYTHON" "$SCRIPT_DIR/tools/quality_scenario_w4_functional_preflight.py" --db "$SOURCE_DB" --require

"$VENV_PYTHON" "$SCRIPT_DIR/tools/openai_mock/server.py" --host "$MOCK_HOST" --port "$MOCK_PORT" >"$MOCK_LOG" 2>&1 &
MOCK_PID=$!
"$VENV_PYTHON" "$SCRIPT_DIR/tools/quality_scenario_functional_provider.py" --host "$MOCK_HOST" --port "$MOCK_PORT"

export LEGACY_QUALITY_ISSUE_DB_PATH="$SOURCE_DB"
export QUALITY_SCENARIO_V1_DB_PATH="$QSV1_DB"
export REVERSE_QUALITY_MODEL_CONFIG="$SCRIPT_DIR/config/runtime/model.w4-functional.yaml"
export W4_FUNCTIONAL_MOCK_API_KEY="test-only-controlled-provider"
export W4_FUNCTIONAL_FIXTURE="1"

"$VENV_PYTHON" "$SCRIPT_DIR/main.py" knowledge-web --db "$SOURCE_DB" --host "$APP_HOST" --port "$APP_PORT" >"$APP_LOG" 2>&1 &
APP_PID=$!

ready=0
for _ in {1..120}; do
  if curl -fsS "http://$APP_HOST:$APP_PORT/issues" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.25
done
if [[ "$ready" != "1" ]]; then
  echo "MAC_DEV_LOOP_START=FAIL"
  echo "APP_LOG=$APP_LOG"
  tail -80 "$APP_LOG" || true
  exit 4
fi

for route in   "/issues"   "/software-assessment"   "/quality-scenarios/workbench"   "/quality-scenarios/library"; do
  curl -fsS "http://$APP_HOST:$APP_PORT$route" >/dev/null
done

echo "MAC_DEV_LOOP_START=PASS"
echo "SOURCE_FIXTURE_DB=$SOURCE_DB"
echo "QSV1_RESULT_DB=$QSV1_DB"
echo "SOURCE_AND_QSV1_DB_SEPARATED=YES"
echo "PROVIDER=CONTROLLED_OPENAI_COMPATIBLE_MOCK"
echo "G1_G5_SOURCE_DATA=READY"
echo "APP_URL=http://$APP_HOST:$APP_PORT/software-assessment#quality-scenario-production"
echo "WORKBENCH_URL=http://$APP_HOST:$APP_PORT/quality-scenarios/workbench"
echo "LIBRARY_URL=http://$APP_HOST:$APP_PORT/quality-scenarios/library"
echo "G5_ADVANCE=./advance_quality_scenario_mac_dev_g5.command"

if [[ "${QUALITY_SCENARIO_DEV_SMOKE_ONLY:-0}" == "1" ]]; then
  echo "MAC_DEV_LOOP_SMOKE=PASS"
  exit 0
fi

if [[ "${QUALITY_SCENARIO_DEV_NO_BROWSER:-0}" != "1" ]] && (( $+commands[open] )); then
  open "http://$APP_HOST:$APP_PORT/software-assessment#quality-scenario-production" || true
fi

echo
echo "开发测试环境已启动。按 Ctrl+C 结束。"
wait "$APP_PID"
