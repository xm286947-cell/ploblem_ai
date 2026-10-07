#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$APP_DIR"

PYTHON_BIN=${PYTHON:-python3}
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 is required. Install Python 3.11 or newer and retry." >&2
  exit 2
fi
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11 or newer is required." >&2
  exit 2
fi

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi
VENV_PYTHON="$APP_DIR/.venv/bin/python"
"$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements-major-mvp-product.txt"

mkdir -p "$APP_DIR/data/quality" "$APP_DIR/data/major/attachments" "$APP_DIR/data/historical_case" "$APP_DIR/data/logs"
echo "Major Production: http://127.0.0.1:${MAJOR_MVP_PORT:-8080}/p0/major-production"
echo "Historical Cases: http://127.0.0.1:${MAJOR_MVP_PORT:-8080}/p0/cases"
echo "Issues / Repeat Risk: http://127.0.0.1:${MAJOR_MVP_PORT:-8080}/p0/issues"
exec "$VENV_PYTHON" "$APP_DIR/scripts/major_mvp_product_start.py" "$@"
