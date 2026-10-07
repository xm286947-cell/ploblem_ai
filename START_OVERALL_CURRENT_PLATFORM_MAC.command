#!/bin/zsh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

MATURE_LAUNCHER="$SCRIPT_DIR/start_quality_capability_p1.command"
if [[ ! -x "$MATURE_LAUNCHER" ]]; then
  echo "MATURE_PLATFORM_LAUNCHER_MISSING=$MATURE_LAUNCHER"
  exit 3
fi

echo "PACKAGE_MODE=MATURE_PLATFORM_FOUNDATION"
echo "MATURE_RUNTIME_ROOT=quality_knowledge.web.app.create_app"
echo "DEFAULT_ENTRY=http://127.0.0.1:18080/issues"
echo "P0_PRODUCT_ENTRY=DISABLED"
echo "P0_CODE_REMOVAL=DEFERRED_UNTIL_DEPENDENCIES_ZERO"

exec "$MATURE_LAUNCHER" --host 127.0.0.1 --port 18080 "$@"
