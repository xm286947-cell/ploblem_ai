#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIXTURE_DB="$SCRIPT_DIR/validation/quality_scenario_w4_fixture.db"

if [[ ! -f "$FIXTURE_DB" ]]; then
  echo "W4 functional fixture database not found:"
  echo "  $FIXTURE_DB"
  echo "This launcher is intended for the packaged macOS functional Golden candidate."
  exit 3
fi

export LEGACY_QUALITY_ISSUE_DB_PATH="$FIXTURE_DB"
export QUALITY_SCENARIO_V1_DB_PATH="$FIXTURE_DB"

echo "W4_MAC_FUNCTIONAL_FIXTURE=YES"
echo "FIXTURE_DB=$FIXTURE_DB"
echo "SYNTHETIC_BUSINESS_DATA=YES"
echo "DIRECT_QSV1_CANDIDATE_WRITE=NO"
echo "DIRECT_QSV1_PUBLISH_WRITE=NO"
echo "REAL_MATURE_DATA_GOLDEN=NO"

exec "$SCRIPT_DIR/start_quality_capability_p1.command" "$@"
