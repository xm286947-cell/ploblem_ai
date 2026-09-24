#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
echo "Hardware Case Product Test V0.1"
echo "P07: http://127.0.0.1:8080/p0/hardware-cases/base-data"
python main.py knowledge-p1-start --db knowledge/quality_capability_p1.db --host 127.0.0.1 --port 8080
