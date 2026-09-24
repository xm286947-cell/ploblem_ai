#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
mkdir -p data/runtime
python scripts/hardware_case_precheck.py --mode web
echo "P07: http://127.0.0.1:8080/p0/hardware-cases/base-data"
python scripts/hardware_case_web_start.py \
  --db data/quality_capability_p1.db \
  --hardware-db data/hardware_case_mvp.db \
  --tree-upload-dir data/hardware_case_tree_uploads \
  --host 127.0.0.1 \
  --port 8080
