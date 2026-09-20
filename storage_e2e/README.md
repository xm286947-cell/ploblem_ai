# Storage × Unified Agent Runtime Integration Acceptance V1.1

Status: FORMAL_REPOSITORY_ACCEPTANCE_ASSET

Baseline before this harness: main@9d54f63b0c97dbbf86b68b0b46791f7d89b36cb3

This directory is a repository-native Storage integration acceptance asset. It is not a temporary ZIP/package.

## Deterministic OpenAI Mock gate

The formal Storage × Unified Runtime × OpenAI Mock matrix lives in
`tests/test_openai_mock_storage_m01_m08.py`:

- M01 normal structured response, Storage schema, Golden, and one atomic commit
- M02 HTTP 429 recovery with Mock counter == Runtime `provider_calls`
- M03 persistent HTTP 503 and hard Retry Budget exhaustion
- M04 connection interruption classified as Runtime transport failure
- M05 truncated JSON followed by validation retry and Golden success
- M06 invalid JSON with no business result or commit
- M07 secret persistence scan while preserving ordinary business fields
- M08 crash/resume with credential re-resolution and exactly one final commit

Mock remains the authoritative deterministic abnormal-path gate.

## STORAGE-REAL-E2E-01

Real Provider is intentionally one opt-in smoke case only:

```
Storage Agent YAML
→ AgentConfigLoader
→ ConfiguredAgentRuntime
→ Storage real-provider domain adapter
→ qwen_prod OpenAI-compatible endpoint
→ StorageFieldResult schema
→ Golden comparison
→ Secret persistence scan
```

Test:

`tests/test_agent_runtime_p0_storage_real_provider_e2e.py`

Required environment:

- `STORAGE_REAL_E2E=1`
- `DASHSCOPE_BASE_URL`
- `DASHSCOPE_API_KEY`

Run locally:

```bash
STORAGE_REAL_E2E=1 \
DASHSCOPE_BASE_URL="<openai-compatible-base-url>" \
DASHSCOPE_API_KEY="<secret>" \
python -m pytest tests/test_agent_runtime_p0_storage_real_provider_e2e.py -q
```

The normal Runtime CI includes this file only as an import/contract check and it
SKIPs while `STORAGE_REAL_E2E` is disabled. A separate manual GitHub Actions
workflow is provided for an explicitly authorized live-provider run.

Real Provider does not re-test 429/503/timeout/truncation. Those belong to Mock.
