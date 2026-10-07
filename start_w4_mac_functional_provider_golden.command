#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/start_w4_mac_functional_golden.command" "$@"
