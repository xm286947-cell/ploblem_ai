# Storage × Unified Agent Runtime Integration Acceptance V1.0

Status: FORMAL_REPOSITORY_ACCEPTANCE_ASSET

Baseline: main@7ef2e947430821de7da160821cdb3dd189377765

This directory is a repository-native Storage integration acceptance asset. It is not a temporary ZIP/package.

Current deterministic acceptance covers:
- normal Storage eMMC structured response
- finish_reason=length -> Runtime VALIDATION retry
- Runtime as the only Retry Owner
- provider call / Retry Budget auditability
- truncated JSON never reaches reviewed business output
- valid JSON passes Storage schema before Atomic Commit
- final field-level Golden comparison

Secret persistence safety is covered in the same Runtime CI by:
- tests/test_runtime_request_secret_persistence.py

Acceptance test:
- tests/test_agent_runtime_p0_storage_e2e_json_truncation.py

Run:
python -m pytest tests/test_agent_runtime_p0_storage_e2e_json_truncation.py tests/test_runtime_request_secret_persistence.py -q

Next step:
- add real-provider Storage E2E after the deterministic integration gate remains green.
