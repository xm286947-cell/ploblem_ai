# Hardware AI Retrieval W0 — OpenSearch Cross-Platform Spike

TASK=HARDWARE-AI-RETRIEVAL-W0-OPENSEARCH-CROSS-PLATFORM-SPIKE-001
STATUS=IMPLEMENTATION_READY_FOR_NATIVE_CI
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

At commit time:

CODE_IMPLEMENTATION=READY
UNIT_TEST=CI_PENDING

MAC_NATIVE_START=CI_PENDING
MAC_ADAPTER=CI_PENDING
MAC_BM25=CI_PENDING
MAC_FILTER=CI_PENDING
MAC_ALIAS=CI_PENDING
MAC_RESTART=CI_PENDING

WINDOWS_NATIVE_START=CI_PENDING
WINDOWS_NO_DOCKER=DESIGN_ENFORCED
WINDOWS_NO_WSL=DESIGN_ENFORCED
WINDOWS_ADAPTER=CI_PENDING
WINDOWS_BM25=CI_PENDING
WINDOWS_FILTER=CI_PENDING
WINDOWS_ALIAS=CI_PENDING
WINDOWS_RESTART=CI_PENDING

FORMAL_KNOWLEDGE_CHANGE=NO
CURRENT_E2E_CHANGE=NO
VECTOR_RAG=NO

No PASS is claimed until the actual native workflow completes successfully.
