#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
sh INIT_LOCAL_CONFIG.sh
python scripts/hardware_case_precheck.py --mode web
host=${HARDWARE_CASE_HOST:-127.0.0.1}
port=${HARDWARE_CASE_PORT:-8080}
echo "P01: http://${host}:${port}/p0/hardware-cases"
python scripts/hardware_case_web_start.py \
  --host "$host" \
  --port "$port"
