#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
mkdir -p data/runtime
python scripts/hardware_case_precheck.py --mode web
echo "P07: http://127.0.0.1:8080/p0/hardware-cases/base-data"
python main.py knowledge-p1-start --db data/quality_capability_p1.db --host 127.0.0.1 --port 8080
