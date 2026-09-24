#!/usr/bin/env bash
set -euo pipefail
python -m pytest -q   tests/test_storage_rc1_mock_assets.py   tests/test_openai_mock_storage_m01_m08.py
