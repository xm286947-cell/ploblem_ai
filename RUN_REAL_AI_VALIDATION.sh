#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
export HARDWARE_CASE_MODEL_CONFIG="${HARDWARE_CASE_MODEL_CONFIG:-$PWD/config/runtime/model.local.yaml}"
export HARDWARE_CASE_RUNTIME_DB="${HARDWARE_CASE_RUNTIME_DB:-$PWD/data/runtime/hardware_case_runtime.db}"
if [ ! -f config/hardware_case_real_validation.local.json ]; then
  echo "[BLOCKED] Run ./INIT_LOCAL_CONFIG.sh first and edit the local validation config."
  exit 2
fi
python scripts/hardware_case_precheck.py --mode real-ai
python tools/hardware_case_real_validation.py --config config/hardware_case_real_validation.local.json
