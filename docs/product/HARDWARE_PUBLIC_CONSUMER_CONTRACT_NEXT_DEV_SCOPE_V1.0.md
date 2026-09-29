# Hardware Public Consumer Contract — Next Development Scope V1.0

TASK=HARDWARE-KNOWLEDGE-NEXT-DEV-GAP-001
STATUS=NEXT_DEV_SCOPE_FROZEN
BASELINE=ae91c6046788cfff7980bc6f6aaeefab2137b3f1
BRANCH=integration/hardware-independent

## P1 Gap
P1_GAP_NAME=Hardware Public Consumer Contract Formalization
GAP_TYPE=DEV
DEV_REQUIRED=YES

## Product scenario
Overall and any future platform consumer need to discover published Hardware Cases, open a case, traverse Circuit/Device classification, and reach Evidence without importing Hardware repositories/services/UI implementation. Hardware remains the product owner; Overall is only a consumer.

## Existing capability — do not rebuild
The independent baseline already preserves:
- published-case Search and Case Detail
- Circuit Tree / Device Tree and cases-by-tree-node
- Evidence and Source Trace
- Word -> AI Candidate -> Review -> Mapping -> Publish production path
- Unified Knowledge publication adapter and RCM-R3 reference/evidence chain
- fail-closed product semantics

Existing read endpoints already expose the required data:
- GET /api/v2/hardware-cases
- GET /api/v2/hardware-cases/{case_id}
- GET /api/v2/hardware-cases/{case_id}/mappings
- GET /api/v2/hardware-cases/{case_id}/evidence
- GET /api/v2/hardware-cases/trees/{tree_type}
- GET /api/v2/hardware-cases/tree-nodes/{node_id}/cases

## Missing capability
There is no separately frozen/versioned public consumer contract artifact that explicitly defines which of the existing Hardware read projections/endpoints are stable for cross-product consumers, their response schemas, visibility semantics, version compatibility, and fail-closed/error semantics. Current hardware-case/v1 is a broader domain contract and includes maintainer/write-side entities; workspace-binding tests also couple navigation to the Overall shell.

USER_ENTRY=No new Hardware UI entry. Existing Hardware Search/Tree/Case Detail remain the product entry; Overall may deep-link to these or consume the public read API.
BACKEND_GAP=No new business backend required. Add a thin public-consumer projection/adapter only if needed to guarantee the frozen response shape without exposing maintainer/internal fields.
CONTRACT_GAP=Create and freeze hardware-public-consumer/v1 covering SEARCH, GET_CASE, GET_TREE, CASES_BY_TREE_NODE, GET_MAPPINGS, GET_EVIDENCE plus visibility/error/version rules.
DATA_GAP=NONE. Reuse published Hardware Case, confirmed mappings, tree snapshots and Evidence already persisted by the Hardware domain.

## Minimal Development Scope
DEV_SCOPE=
1. Add schema/contract artifact hardware-public-consumer/v1.
2. Define public DTO/projection from existing HardwareCaseContractService consumer projections; no new persistence.
3. Bind the existing read APIs to the frozen public contract (or add a thin adapter facade if changing existing response shape would risk regression).
4. Freeze consumer visibility: PUBLISHED only by default; historical DEPRECATED only when explicitly requested.
5. Freeze Evidence/Mapping exposure and fail-closed codes for missing/non-visible resources.
6. Add contract tests proving an external consumer can Search -> Case Detail -> Mapping/Tree -> Evidence using public DTOs only.
7. Add a boundary regression proving the public consumer layer does not import Overall implementation and does not create Runtime/Knowledge forks.
8. Keep current Hardware UI and all 14 preserved capabilities unchanged.

OUT_OF_SCOPE=
- new Hardware product features
- UED redesign
- Overall navigation redesign
- Runtime/Knowledge changes
- new database/storage
- rewriting hardware-case/v1
- #200 test evidence remediation

## Acceptance
ACCEPTANCE=
- hardware-public-consumer/v1 schema validates all frozen read responses.
- Search -> Detail -> Tree/Mapping -> Evidence contract test PASS.
- Consumer cannot see non-published cases unless historical semantics explicitly permit DEPRECATED.
- No maintainer/write-side fields or operations become required for Overall consumption.
- Hardware-only composition PASS.
- Existing Hardware focused regression remains PASS.
- RUNTIME_REUSE=YES and KNOWLEDGE_REUSE=YES.
- Overall implementation is not imported by the Hardware public-consumer layer.
- CAPABILITY_PRESERVED remains 14/14.

BASELINE_CHANGED=NO
READY_FOR_IMPLEMENTATION=YES
BLOCKER=NONE
