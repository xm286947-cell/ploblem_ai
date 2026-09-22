#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ${1:-} != "" ]]; then
  export MAJOR_MODEL_CONFIG="$1"
elif [[ ! -f "config/model.local.yaml" ]]; then
  echo "[ERROR] Runtime model config not found."
  echo "Usage: ./run_major_real_truncation_e2e.sh <path-to-model.local.yaml>"
  echo "Or place a non-committed config/model.local.yaml in the project."
  exit 2
fi

export MAJOR_D01_TRUNCATION_REAL_E2E=1

mkdir -p test-results/major-real-provider

python -m pytest -q   tests/test_major_d01_real_truncation_e2e.py::test_major_d01_real_provider_truncation_recovers_by_replanning   --junitxml=test-results/major-real-provider/truncation-golden.xml

echo "[PASS] Major D01 Real Truncation Golden"
