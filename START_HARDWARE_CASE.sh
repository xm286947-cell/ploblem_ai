#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python scripts/hardware_case_precheck.py --mode web
echo "P01: http://127.0.0.1:8080/p0/hardware-cases"
python scripts/hardware_case_web_start.py \
  --host 127.0.0.1 \
  --port 8080
