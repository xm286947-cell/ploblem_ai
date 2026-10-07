# Overall Current Platform macOS Functional Candidate

This package starts from the **Overall** composition root, not the mature Quality-only host.

Default entry:

```text
/p0/overall
```

The same FastAPI host exposes the four product domains:

- Major Problem / Repeat Risk
- Hardware Case
- Quality Scenario
- Storage Lifetime

The W4 synthetic database is only a **supporting legacy Quality source fixture** for exercising the Quality Scenario workflow on macOS. It is not the package identity and it does not pre-seed QSV1 Candidate / Review / Published state.

Runtime binding:

```text
PRIMARY_P0_DB=validation/overall_current_platform_p0.db
LEGACY_QUALITY_ISSUE_DB_PATH=validation/quality_scenario_w4_fixture.db
QSV1_LIFECYCLE_STORE=PRIMARY_P0_DB
DEFAULT_ENTRY=/p0/overall
```

Start:

```bash
./START_OVERALL_CURRENT_PLATFORM_MAC.command --host 127.0.0.1 --port 18080
```

Validation classification:

```text
PACKAGE_SCOPE=FULL_CURRENT_PLATFORM
QUALITY_SCENARIO_STANDALONE_PACKAGE=NO
QS_W4_FIXTURE_INCLUDED=YES
QS_W4_FIXTURE_ROLE=SUPPORTING_TEST_SOURCE
REAL_MATURE_DATA_COMPATIBILITY=DEFERRED
```
