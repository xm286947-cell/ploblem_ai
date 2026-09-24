# Storage RC1 Test Assets

Status: TEST_ASSET_BASELINE_DRAFT

Authority:
- Product baseline: `PRODUCT_REQUIREMENT_BASELINE_STORAGE_MVP_RC1_V1.0`
- Test Expected: `STORAGE_EXPECTED_BASELINE_RC1_V0.1`
- Center rule: product manager defines scenarios; Storage test team defines tests; Work executes frozen assets only.

## What is implemented here

1. `fixtures/M01...M22`
   - Deterministic OpenAI Mock responses for Identity, Parameter/Coverage, Evidence, Schema errors, Provider faults, Semantic Repair, Human Review authority, Knowledge Production, Conflict and Diagnostic-no-runtime-value.
2. `mock_harness.py`
   - Reuses the repository's existing `tools/openai_mock/server.py`.
   - Mock replaces Provider/AI Response only.
   - Repository, DB, Review, Publish, Query and Consumer are never mocked by this harness.
3. `tests/test_storage_rc1_mock_assets.py`
   - Fixture completeness and governance.
   - Coverage five-state semantics.
   - AI cannot Confirm or Publish.
   - Runtime value cannot be fabricated.
   - Real OpenAI Mock HTTP behavior for normal responses, 429 and truncated JSON.
   - Authorization is redacted in the Mock request ledger.
4. `scenario_catalog.json` + `tests/test_storage_rc1_scenario_catalog.py`
   - Maps REQ-STG-001..015 and Golden A/B/C to system cases and Mock fixtures.
5. `run_mock_gate.py/.sh/.bat`
   - Deterministic Mock gate.
6. `run_full_gate.py`
   - Full RC1 runner.
   - If the SUT package-specific tests are absent, it exits BLOCKED rather than silently reporting PASS.

## Run

Mock/fixture gate:

```bash
python test_assets/storage_rc1/run_mock_gate.py
```

or:

```bash
python test_assets/storage_rc1/run_full_gate.py --mock-only
```

Full RC1 gate after overlaying these assets into the frozen Storage SUT package:

```bash
python test_assets/storage_rc1/run_full_gate.py
```

The full gate intentionally requires:
- `tests/test_storage_product_mvp_rc1.py`
- `tests/test_storage_rc1_review_blocker_001.py`

Their absence is `BLOCKED`, not `PASS`.

## Scenario coverage

- Golden A: M01-M18 + Storage package product tests.
- Golden B: M19-M21 + existing Knowledge Production review/publish/release tests.
- Golden C: M03/M07/M20/M21/M22 + Storage package compare/diagnose/impact tests.
- Provider abnormal path: M14 429, M15 timeout, M16 truncation, M17 semantic repair.
- Governance: M18 prohibits AI confirm; M19 prohibits AI publish.
- Evidence: M10 missing, M11 invalid reference.
- Schema: M12 required field missing, M13 wrong type.

## Gate rule

`Mock Gate PASS != Product Test Gate PASS`.

A Product Test Gate can pass only when Golden A/B/C, REQ-STG-001..015, browser/system evidence and all P0 execution evidence are complete. Any FAIL, BLOCKED or NOT_TESTED on a P0 item prevents Product Test Gate PASS.
