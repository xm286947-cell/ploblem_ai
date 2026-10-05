#!/bin/sh
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$APP_DIR/START_MAJOR_MVP.sh" "$@"
