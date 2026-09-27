# Overall VNext Synthetic Data + OpenAI Mock Validation

TASK=OVERALL-VNEXT-SYNTHETIC-OPENAI-MOCK-VALIDATION-001
STATUS=PASS
BRANCH=demo/overall-vnext-fast-mvp
REAL_CUSTOMER_DATA=NO
REAL_PROVIDER_CALL=NO

## Synthetic product data

- P04 fixture: `QS-FIX-002` with source `PROBLEM-003`.
- Legacy Quality Issue fixture: `ITR-VNEXT-DEMO-001` in an isolated SQLite database.
- The repository's PLC Mapping baseline is explicitly migrated and activated in that isolated database before import.
- Four Overall task-provider items cover Major, Quality Scenario, Hardware, and Storage navigation.
- P0, Legacy, Hardware, Storage, uploads, sources, and Knowledge Production use isolated data directories.

## OpenAI Mock coverage

| Area | Evidence |
| --- | --- |
| Unified Runtime + real HTTP Mock | PASS |
| Major Case Runtime | PASS |
| Repeat Runtime | PASS |
| Hardware Case Runtime adapter | PASS |
| Knowledge Production extraction | PASS |
| Storage Mock E2E | PASS |
| Mock server protocol | PASS |
| Official Python SDK compatibility | PASS |
| Streaming and disconnect behavior | PASS |
| Secret persistence and resume | PASS |
| Provider endpoint contract | PASS |

## Results

- Domain Runtime and product Mock matrix: **29 passed**.
- Mock protocol, SDK, streaming, secret/resume, and Provider Contract matrix: **49 passed**.
- Overall synthetic product startup and route/data verification: **PASS**.
- Total automated Mock assertions in the two pytest matrices: **78 passed**.

The test process clears inherited proxy variables only for localhost Mock calls and sets `NO_PROXY=127.0.0.1,localhost`. This prevents a workstation or cloud SOCKS proxy from intercepting local Mock traffic. Product configuration is unchanged.

## Reproduce

Install the repository's frozen Mock test dependencies:

```bash
python -m pip install -r requirements-openai-mock-test.txt
```

Run the complete validation:

```bash
python scripts/overall_vnext_openai_mock_validation.py \
  --data-dir /tmp/overall-vnext-openai-mock-validation
```

The generated databases and fixtures are synthetic validation assets. This result does not replace a real Provider acceptance run, formal S11 regression, or Product Test Gate.
