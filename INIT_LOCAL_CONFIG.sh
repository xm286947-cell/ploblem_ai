#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -f config/runtime/model.local.yaml ]; then
  cp config/runtime/model.local.hardware_case.example.yaml config/runtime/model.local.yaml
  echo "[CREATED] config/runtime/model.local.yaml"
fi
if [ ! -f config/hardware_case_real_validation.local.json ]; then
  cp config/hardware_case_real_validation.local.example.json config/hardware_case_real_validation.local.json
  echo "[CREATED] config/hardware_case_real_validation.local.json"
fi
mkdir -p data/input/word data/tree data/output data/runtime
echo "Local configuration initialized."
