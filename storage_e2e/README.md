# Storage × Unified Agent Runtime Integration Acceptance V1.0

Status: FORMAL_REPOSITORY_ACCEPTANCE_ASSET

Baseline: main@169f6d59e94c3291a48bb029265d0910de7bfb48

This directory is a repository-native Storage integration acceptance asset. It is not a temporary ZIP/package.

Current deterministic acceptance covers the formal Storage × Unified Runtime ×
OpenAI Mock matrix in `tests/test_openai_mock_storage_m01_m08.py`:

- M01 normal structured response, Storage schema, Golden, and one atomic commit
- M02 HTTP 429 recovery with Mock counter == Runtime `provider_calls`
- M03 persistent HTTP 503 and hard Retry Budget exhaustion
- M04 connection interruption classified as Runtime transport failure
- M05 truncated JSON followed by validation retry and Golden success
- M06 invalid JSON with no business result or commit
- M07 secret persistence scan with business fields named `password`, `api_key`, and `token`
- M08 crash/resume with credential re-resolution and exactly one final commit

The existing `test_agent_runtime_p0_storage_e2e_json_truncation.py` remains the
focused Storage integration acceptance for schema and Golden semantics.

Secret persistence safety is covered in the same Runtime CI by:
- tests/test_runtime_request_secret_persistence.py

Acceptance test:
- tests/test_openai_mock_storage_m01_m08.py
- tests/test_agent_runtime_p0_storage_e2e_json_truncation.py

Run:
python -m pytest tests/test_agent_runtime_p0_storage_e2e_json_truncation.py tests/test_runtime_request_secret_persistence.py -q

Real provider:
- `STORAGE-REAL-E2E-01` is an opt-in, one-case smoke path owned by the consumer
  of the repository. CI deliberately skips it when its provider secret is absent;
  no external model is used by the Mock acceptance gate.
