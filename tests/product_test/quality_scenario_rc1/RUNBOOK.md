# QUALITY SCENARIO RC1 — Test Runbook V0.1

## Preconditions

1. Checkout the frozen test branch/commit from TEST_EXECUTION_MANIFEST.json.
2. Python environment must satisfy the repository's existing RC1 dependencies, including FastAPI/TestClient and Unified Agent Runtime.
3. Do not provide real internal ITR data. The repository test set is SANITIZED_SYNTHETIC only.
4. No external OpenAI endpoint is required. The tests start a local OpenAI-compatible mock on 127.0.0.1 with an ephemeral port.
5. Do not modify product code, fixture JSON, Expected JSON, or tests during execution.

## Commands

Primary product-test baseline:

```bash
python -m pytest -q tests/product_test/quality_scenario_rc1/test_quality_scenario_product_baseline.py
```

Existing engineering compatibility set:

```bash
python -m pytest -q   tests/test_quality_scenario_candidate_v1_service.py   tests/test_quality_scenario_v1_workflow_service.py   tests/test_quality_scenario_traceability.py   tests/test_quality_scenario_rc1_golden_e2e.py
```

Provider-fault case only:

```bash
python -m pytest -q   tests/product_test/quality_scenario_rc1/test_quality_scenario_product_baseline.py   -m provider_fault
```

## Evidence to retain

- pytest summary and failed test IDs
- stack trace for any failed case
- git commit SHA
- mock fixture version
- Expected version
- temporary DB inspection only when needed for failure diagnosis; do not commit DB files
- no API key, Authorization header, internal problem text, or production data in uploaded logs

## Gate result

PASS: all mandatory cases pass and evidence is complete.  
FAIL: any executed mandatory case fails.  
BLOCKED: dependency/environment prevents execution.  
NOT_TESTED: not executed; never equivalent to PASS.

Work must return evidence only. It must not fix code or change Expected in the same execution task.
