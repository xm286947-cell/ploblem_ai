#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec /bin/zsh "$SCRIPT_DIR/start_quality_scenario_mac_dev_loop.command" "$@"
