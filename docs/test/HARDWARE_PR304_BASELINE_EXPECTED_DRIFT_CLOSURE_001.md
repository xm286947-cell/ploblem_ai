# PR #304 Baseline Expected Drift Closure

TASK=HARDWARE-PR304-BASELINE-EXPECTED-DRIFT-CLOSURE-001
STATUS=EXPECTED_DRIFT_CORRECTED

## Canonical evidence
Canonical main `quality_knowledge/web/p0_app.py` sets `root_target = "/p0/issues"` when QUALITY_ISSUE is enabled, and the root route redirects to that target.

## Drift
The Hardware product API E2E test still expected the superseded platform root:
`/ -> /p0/insights`.

## Correction
Only the stale Expected in `tests/test_hardware_case_product_api_e2e.py` was changed:
`/p0/insights` -> `/p0/issues`.

PRODUCT_CODE_CHANGED=NO
PUBLIC_CONSUMER_CONTRACT_CHANGED=NO
ROOT_ROUTE_CHANGED=NO
ASSERTION_WEAKENED=NO
OTHER_TEST_CHANGED=NO
NEW_FEATURE_DEV=NO

NEXT=RERUN_PR_304_GATE
