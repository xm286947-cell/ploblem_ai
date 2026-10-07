#!/bin/bash
set -e
cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" tools/hardware_retrieval_demo_seed.py --execute-demo
printf "\nDemo data loaded. Open: http://127.0.0.1:8080/p0/hardware-cases/search?q=%E6%9C%89%E5%93%AA%E4%BA%9Bmcu%E7%9A%84%E9%97%AE%E9%A2%98\n"
read -r -p "Press Enter to close..."
