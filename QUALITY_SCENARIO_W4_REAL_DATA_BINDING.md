# Quality Scenario W4 Real-Data Browser Golden

This package intentionally does not embed synthetic business data.

## Required source
Use a copy of the mature Quality Issue SQLite database that contains:
- quality_issue rows
- SOFTWARE_OPERATION source facts
- established SOFTWARE_OPERATION → quality_issue links
- supporting ITR_CS / ESCAPE_ANALYSIS facts where available

Do not run Browser Golden against the original production/master database.
Create a validation copy first.

## macOS
```bash
cp "/absolute/path/to/mature/quality_issue_v1.db" "./validation_quality_issue_v1.db"
export LEGACY_QUALITY_ISSUE_DB_PATH="$PWD/validation_quality_issue_v1.db"
export QUALITY_SCENARIO_V1_DB_PATH="$LEGACY_QUALITY_ISSUE_DB_PATH"
./.venv/bin/python tools/quality_scenario_real_data_preflight.py --db "$LEGACY_QUALITY_ISSUE_DB_PATH" --require
./start_quality_capability_p1.command --port 18080
```

## Windows
```bat
copy "C:\absolute\path\to\mature\quality_issue_v1.db" ".\validation_quality_issue_v1.db"
set "LEGACY_QUALITY_ISSUE_DB_PATH=%CD%\validation_quality_issue_v1.db"
set "QUALITY_SCENARIO_V1_DB_PATH=%LEGACY_QUALITY_ISSUE_DB_PATH%"
.venv\Scripts\python.exe tools\quality_scenario_real_data_preflight.py --db "%LEGACY_QUALITY_ISSUE_DB_PATH%" --require
start_quality_capability_p1.bat --port 18080
```

## Gate
Browser Golden may start only when:
```text
QUALITY_ISSUE_COUNT>0
SOFTWARE_OPERATION_SOURCE_FACT_COUNT>0
LINKED_SOFTWARE_OPERATION_COUNT>0
REAL_DATA_PREFLIGHT=PASS
```

Candidate/Confirmed/Published QSV1 rows created during validation stay in the validation copy.
