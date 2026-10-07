# Hardware AI Retrieval W0 — OpenSearch Cross-Platform Spike

TASK=HARDWARE-AI-RETRIEVAL-W0-OPENSEARCH-CROSS-PLATFORM-SPIKE-001
STATUS=W0_PASS
ARCH_BASELINE=HARDWARE_KNOWLEDGE_AI_RETRIEVAL_SYSTEM_ARCHITECTURE_V0.1
BRANCH=spike/hardware-ai-retrieval-opensearch-w0
BASE=dfde46e01f654a5d77187d393b159bdac84f08fd

## Goal

Prove that OpenSearch can remain a replaceable, rebuildable retrieval sidecar
while the same Python HTTP adapter works on:

- macOS native development/debug
- Windows native demo deployment without Docker or WSL

Linux portability remains architectural only in W0.

## Frozen boundaries

W0 does not change:

- Formal Knowledge
- Stage A / Stage B
- prompts or hardware-case-knowledge-object/v1
- Unified Runtime
- Unified Knowledge
- hardware-knowledge-consumption/v1
- the current product search UI
- the current Hardware R1 Final E2E / Golden Expected state

No Vector, Embedding, RAG, Haystack runtime, or Tree prerequisite is introduced.

## Version

OpenSearch 3.9.0.

The W0 launchers use the Security plugin disabled on localhost only. This is
strictly a local development/demo configuration and is not a production
security recommendation.

## Added W0 components

- services/hardware_search_adapter.py
- config/hardware_search.example.yaml
- scripts/opensearch/start_macos.command
- scripts/opensearch/start_windows.bat
- scripts/opensearch/smoke_search.py
- tests/test_hardware_search_adapter.py
- .github/workflows/hardware-ai-retrieval-opensearch-w0.yml

OpenSearch binaries are never committed to the repository.

## W0 synthetic smoke corpus

The native smoke uses four synthetic/sanitized documents only:

1. MCU intermittent reset / power-cycle recovery
2. CAN communication interruption
3. Operational amplifier batch defect
4. LDO output oscillation

The smoke validates:

- native engine health
- index creation
- idempotent index detection
- document upsert
- BM25 text query
- exact metadata filter
- alias cutover between index generations
- persisted queryability after process restart

## Architecture boundary

The adapter is OS-independent and only speaks HTTP.

macOS launcher and Windows launcher both start localhost OpenSearch, and both
feed the same Python HTTP adapter.

The adapter does not import or open Formal Knowledge, Candidate, Source, Runtime,
or Consumption databases.

## Stable W0 error codes

SEARCH_ENGINE_URL_INVALID
SEARCH_ENGINE_TIMEOUT_INVALID
SEARCH_ENGINE_AUTH_CONFIG_INVALID
SEARCH_ENGINE_AUTH_FAILED
SEARCH_ENGINE_TIMEOUT
SEARCH_ENGINE_UNAVAILABLE
SEARCH_ENGINE_RESPONSE_INVALID
SEARCH_ENGINE_REQUEST_FAILED
SEARCH_INDEX_NOT_FOUND
SEARCH_INDEX_BUILD_FAILED
SEARCH_QUERY_FAILED
SEARCH_INDEX_NAME_INVALID
SEARCH_ALIAS_NAME_INVALID
SEARCH_DOCUMENT_ID_REQUIRED
SEARCH_LIMIT_INVALID

These codes are intended to support a later upper-layer fallback to the existing
SQLite V0 retrieval path. W0 itself does not wire product fallback.

## Native CI gate

The dedicated workflow contains three jobs:

- adapter unit test on Ubuntu
- macOS native OpenSearch tarball + Java 21
- Windows native OpenSearch ZIP using its bundled JDK

Both native jobs must execute the same smoke_search.py, then restart the
OpenSearch process and verify the active alias remains queryable.

## Gate status

W0_ARCHITECTURE_GATE=PASS
W0_DEDICATED_WORKFLOW=Hardware AI Retrieval OpenSearch W0
W0_EVIDENCE_RUN=37572530581
W0_EVIDENCE_HEAD=e09d4c21118d9ecc43cadf4f964d6e61d1f27d1d

CODE_IMPLEMENTATION=PASS
UNIT_TEST=PASS

MAC_NATIVE_START=PASS
MAC_ADAPTER=PASS
MAC_BM25=PASS
MAC_FILTER=PASS
MAC_ALIAS=PASS
MAC_RESTART=PASS

WINDOWS_NATIVE_START=PASS
WINDOWS_NO_DOCKER=PASS
WINDOWS_NO_WSL=PASS
WINDOWS_ADAPTER=PASS
WINDOWS_BM25=PASS
WINDOWS_FILTER=PASS
WINDOWS_ALIAS=PASS
WINDOWS_RESTART=PASS

MAC_RESTART_PERSISTENCE=PASS
WINDOWS_RESTART_PERSISTENCE=PASS

INHERITED_HARDWARE_CASE_PRODUCT_TEST_PACKAGE=INHERITED_BASELINE_FAILURE
INHERITED_HARDWARE_W31_FRESH_EXTRACT=INHERITED_BASELINE_FAILURE
INHERITED_BASELINE_FAILURE_CAUSE=repositories.json_repository missing from the pre-existing Hardware R1 package closure

FORMAL_KNOWLEDGE_CHANGE=NO
CURRENT_E2E_CHANGE=NO
VECTOR_RAG=NO

The W0 PASS is limited to this cross-platform OpenSearch spike. It does not
release W1 and does not change the current Hardware R1 Final E2E gate.
