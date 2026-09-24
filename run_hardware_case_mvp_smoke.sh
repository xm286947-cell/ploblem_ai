#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python -m pip install -r requirements.txt -r requirements-runtime-p0-test.txt
python scripts/hardware_case_mvp_smoke.py
