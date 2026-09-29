#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
mkdir -p data/runtime
python scripts/hardware_case_precheck.py --mode web
echo "P01: http://127.0.0.1:8080/p0/hardware-cases"
python scripts/hardware_case_web_start.py \
  --db data/quality_capability_p1.db \
  --hardware-db data/hardware_case_mvp.db \
  --tree-upload-dir data/hardware_case_tree_uploads \
  --source-root data/hardware_case_sources \
  --host 127.0.0.1 \
  --port 8080
