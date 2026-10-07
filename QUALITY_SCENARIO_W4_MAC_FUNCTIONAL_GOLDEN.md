# W4 macOS Functional Golden — Controlled Test Data

This validation path exists because mature real business data is not available on macOS.

## Scope

This fixture is allowed for **functional Golden** only.

It creates:
- 5 Quality Issue source records
- 5 Software Assessment source facts
- Resolution source facts
- Missed-test source facts/effective analysis where required
- source links and one controlled conflict

It does **not** create:
- QSV1 Candidate
- Review/Confirm state
- Published QSV1
- Portrait result

Those outcomes must be produced by the product itself.

## Build fixture

```bash
./.venv/bin/python tools/build_quality_scenario_test_fixture.py \
  --db "$PWD/validation/quality_scenario_w4_fixture.db"
```

Bind both mature source and QSV1 lifecycle to the dedicated validation copy:

```bash
export LEGACY_QUALITY_ISSUE_DB_PATH="$PWD/validation/quality_scenario_w4_fixture.db"
export QUALITY_SCENARIO_V1_DB_PATH="$LEGACY_QUALITY_ISSUE_DB_PATH"
./start_quality_capability_p1.command --port 18080
```

Expected source-side cases:

- G1_COMPLETE: Assessment + Resolution + effective missed-test analysis
- G2_NO_MISSED_TEST: Assessment + Resolution, missed-test explicitly missing
- G3_CONFLICT: two conflicting Resolution sources, must fail closed
- G4_DUPLICATE_GENERATE: generate the same frozen source twice
- G5_SOURCE_REVISION: create V1 first, then advance Resolution source to V2

## G5 revision step

After the first G5 Candidate exists:

```bash
./.venv/bin/python tools/build_quality_scenario_test_fixture.py \
  --db "$PWD/validation/quality_scenario_w4_fixture.db" \
  --advance-g5
```

Refresh Software Assessment. The product should surface source change / re-analysis lineage without overwriting the earlier result.

## Validation classification

```text
MAC_FUNCTIONAL_GOLDEN=ALLOWED_WITH_CONTROLLED_FIXTURE
REAL_MATURE_DATA_COMPATIBILITY=DEFERRED
SYNTHETIC_FIXTURE_IS_NOT_REAL_DATA_GOLDEN=YES
```
