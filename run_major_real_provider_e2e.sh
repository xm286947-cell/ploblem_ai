#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ${1:-} != "" ]]; then
  export MAJOR_MODEL_CONFIG="$1"
elif [[ ! -f "config/model.local.yaml" ]]; then
  echo "[ERROR] Runtime model config not found."
  echo "Usage: ./run_major_real_provider_e2e.sh <path-to-model.local.yaml>"
  echo "Or place a non-committed config/model.local.yaml in the project."
  exit 2
fi

export MAJOR_REAL_E2E=1
export MAJOR_ISSUE_REAL_E2E=1
export MAJOR_REPEAT_REAL_E2E=1

mkdir -p test-results/major-real-provider

python -m pytest -q   tests/test_rcfg02_major_runtime_config.py::test_rcfg02_major_occurrence_real_provider_golden_smoke   tests/test_major_d01_real_provider_e2e.py::test_major_d01_real_provider_uses_runtime_model_config   tests/test_repeat_real_provider_e2e.py::test_repeat_real_provider_golden_uses_runtime_model_config   --junitxml=test-results/major-real-provider/golden.xml

echo "[PASS] Major Real Provider Golden: occurrence + D01 + Repeat Decision"
