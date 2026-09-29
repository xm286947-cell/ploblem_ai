# Hardware Knowledge System Requirements — W1 Baseline V1.0

TASK=HARDWARE-SYSTEM-REQUIREMENTS-W1-001
STATUS=SYSTEM_REQUIREMENTS_BASELINE_FROZEN
BASELINE_MAIN=633224cbcc8f91e63a62226c8720df76c9758f49
PRODUCT_BASELINE=HARDWARE_INDEPENDENT_BASELINE
PUBLIC_CONTRACT=hardware-public-consumer/v1
FEATURE_DEVELOPMENT=HOLD

## 1. Purpose

This document freezes the system-level requirements for the independently developable Hardware Knowledge / Hardware Case product after PR #304.

W1 does not add product features. It translates the existing product baseline and 14 preserved capabilities into system requirements that W2 architecture and W3 implementation must obey.

## 2. System mission

The system shall support the complete Hardware Case lifecycle:

```text
Word / approved source
  -> AI Structure Candidate
  -> Evidence grounding
  -> Human Review
  -> Circuit Tree / Device Tree mapping
  -> Publish Gate
  -> Unified Knowledge publication
  -> Search / Tree / Case Detail / Evidence
  -> Public Consumer Contract
```

The product shall remain independently startable and testable while reusing shared platform Runtime, Knowledge, and Web hosting infrastructure.

## 3. System boundary

### 3.1 Owned by Hardware domain

Hardware owns:
- Hardware case schema and lifecycle.
- Word intake and source registry.
- AI structured candidate semantics.
- Evidence grounding and source trace.
- Human review semantics.
- Circuit Tree and Device Tree semantics.
- Excel tree import and tree version/application rules.
- Dual-tree mapping.
- Hardware Publish Gate.
- Hardware product UI and read/write APIs.
- Hardware public-consumer projection.
- Hardware product persistence.

### 3.2 Shared platform dependencies

Hardware shall reuse, not fork:
- Unified Runtime.
- Unified Knowledge.
- Shared FastAPI/Web host.
- Public contract mechanisms.

### 3.3 External consumers

Overall and future consumers shall consume Hardware only through:
- `hardware-public-consumer/v1`, and/or
- stable deep links into Hardware-owned UI.

Hardware shall not import Overall-owned product implementation.

## 4. Functional system requirements

| ID | Requirement | W1 State |
|---|---|---|
| SYS-FUN-001 | System shall ingest approved Hardware Case Word sources and retain source identity. | SATISFIED |
| SYS-FUN-002 | System shall invoke Unified Runtime for AI structuring and persist Candidate separate from Confirmed values. | SATISFIED |
| SYS-FUN-003 | AI output shall never auto-publish. | SATISFIED |
| SYS-FUN-004 | System shall allow human confirmation/rejection of candidate facts. | SATISFIED |
| SYS-FUN-005 | System shall retain Evidence and Source Trace for confirmed knowledge. | SATISFIED |
| SYS-FUN-006 | System shall manage Circuit/Feature and Material/Device trees as independent classifications. | SATISFIED |
| SYS-FUN-007 | System shall support versioned Excel import for both trees. | SATISFIED |
| SYS-FUN-008 | System shall support confirmed dual-tree mapping and preserve stable case identity across mappings. | SATISFIED |
| SYS-FUN-009 | Publish shall fail closed when required review, Evidence, or mapping conditions are unmet. | SATISFIED |
| SYS-FUN-010 | Published cases shall be discoverable through Search, Tree navigation, Case Detail, and Evidence. | SATISFIED |
| SYS-FUN-011 | Consumer surfaces shall expose PUBLISHED by default; DEPRECATED shall require explicit historical access. | SATISFIED |
| SYS-FUN-012 | System shall publish approved Hardware knowledge through Unified Knowledge public contracts only after Hardware Publish Gate PASS. | SATISFIED |
| SYS-FUN-013 | System shall expose stable cross-product read semantics through `hardware-public-consumer/v1`. | SATISFIED |
| SYS-FUN-014 | Public consumer operations shall include SEARCH, GET_CASE, GET_TREE, CASES_BY_TREE_NODE, GET_MAPPINGS, GET_EVIDENCE. | SATISFIED |

## 5. Independence and integration requirements

| ID | Requirement | W1 State |
|---|---|---|
| SYS-BND-001 | Hardware shall boot in HARDWARE_CASE-only composition without Overall, Repeat Risk, Major Case, or other business domains. | SATISFIED |
| SYS-BND-002 | Hardware shall not create a second Web application/server/port for product independence. | SATISFIED |
| SYS-BND-003 | Hardware shall not fork Unified Runtime. | SATISFIED |
| SYS-BND-004 | Hardware shall not fork Unified Knowledge. | SATISFIED |
| SYS-BND-005 | Overall shall remain a consumer/integration shell, not Hardware implementation owner. | SATISFIED |
| SYS-BND-006 | Cross-product consumption shall not require Hardware repository/service/private DB imports. | SATISFIED |
| SYS-BND-007 | W2 shall freeze the concrete deployment binding for `hardware-public-consumer/v1` to external consumers without changing Hardware business semantics. | W2_INPUT |
| SYS-BND-008 | W2 shall define version compatibility/deprecation rules for future `hardware-public-consumer/vN` evolution. | W2_INPUT |

## 6. Data and persistence requirements

| ID | Requirement | W1 State |
|---|---|---|
| SYS-DATA-001 | Hardware product data shall remain in Hardware-owned persistence, separate from platform control data. | SATISFIED |
| SYS-DATA-002 | Source files shall remain company-local and shall not be bundled into product/test packages. | SATISFIED |
| SYS-DATA-003 | Browser/API consumer surfaces shall not expose absolute server filesystem paths. | SATISFIED |
| SYS-DATA-004 | Source hash/integrity mismatch shall fail closed. | SATISFIED |
| SYS-DATA-005 | Tree import Apply shall remain atomic and versioned. | SATISFIED |
| SYS-DATA-006 | Formal tree nodes shall not be physically deleted through P07; lifecycle shall use supported state/version semantics. | SATISFIED |
| SYS-DATA-007 | W2 shall define database/schema versioning and upgrade compatibility for independent product releases. | W2_INPUT |
| SYS-DATA-008 | W2 shall define backup/restore and rollback boundaries for Hardware-owned DB, source registry, and tree import state. | W2_INPUT |

## 7. Evidence and fail-closed requirements

The following are mandatory invariants:
- Missing or unavailable Evidence shall not silently become valid knowledge.
- Missing/non-visible cases shall return fail-closed behavior.
- Candidate values shall never leak into consumer effective facts unless human-confirmed.
- Missing/ambiguous Public Ref and Evidence mismatch shall fail closed.
- Knowledge unavailability shall not silently fall back to a divergent Hardware-local published knowledge path.
- SOURCE_UNAVAILABLE may preserve an already published case but must surface explicit warning state.
- Both tree sides UNMAPPED shall block publish.
- A confirmed mapping on at least one tree may satisfy the current minimum mapping gate.

These semantics are baseline behavior and are not open for W2 redesign unless a separate product decision explicitly changes them.

## 8. Runtime and AI requirements

| ID | Requirement | W1 State |
|---|---|---|
| SYS-AI-001 | Hardware AI shall run through Unified Runtime agent `hardware_case.structure`. | SATISFIED |
| SYS-AI-002 | Provider configuration/secrets shall stay outside committed product data and source packages. | SATISFIED |
| SYS-AI-003 | Runtime failure shall not corrupt the last valid Hardware business state. | SHARED_RUNTIME_BASELINE |
| SYS-AI-004 | AI result shall remain Candidate until human review. | SATISFIED |
| SYS-AI-005 | W2 shall define end-to-end correlation/trace identity across Intake -> Runtime -> Review -> Publish -> Knowledge for supportability. | W2_INPUT |

## 9. Deployment and platform requirements

| ID | Requirement | W1 State |
|---|---|---|
| SYS-DEP-001 | Windows shall have a product launcher using the same Hardware-only composition. | SATISFIED |
| SYS-DEP-002 | POSIX/macOS shall be able to start the same Hardware-only composition using the provided shell launcher. | SATISFIED |
| SYS-DEP-003 | Windows and macOS shall use the same business routes, persistence semantics, contracts, and Runtime/Knowledge dependencies. | REQUIRED |
| SYS-DEP-004 | Product default entry shall remain `/p0/hardware-cases`. | SATISFIED |
| SYS-DEP-005 | Product shall default to loopback/local hosting unless explicitly configured otherwise. | SATISFIED |
| SYS-DEP-006 | W2 shall decide whether macOS requires a Finder double-click `.command` entry in addition to the POSIX shell launcher. | W2_INPUT |
| SYS-DEP-007 | W2 shall define package/runtime dependency ownership so launchers do not drift by OS. | W2_INPUT |

## 10. Security requirements

| ID | Requirement | W1 State |
|---|---|---|
| SYS-SEC-001 | Real Word/Excel/images, runtime DBs, logs, API keys, Authorization values, provider raw content and local model config shall not be committed to GitHub or bundled unintentionally. | SATISFIED_POLICY |
| SYS-SEC-002 | Product source access shall be controlled by source registry semantics rather than arbitrary filesystem exposure. | SATISFIED |
| SYS-SEC-003 | Maintainer and consumer permissions shall remain semantically separated. | SATISFIED_MVP |
| SYS-SEC-004 | W2 shall define production-grade authentication/authorization boundary; internal-test role headers are not by themselves a production IAM design. | W2_INPUT |
| SYS-SEC-005 | W2 shall define secret injection/rotation responsibilities across Runtime and Hardware deployment. | W2_INPUT |

## 11. Reliability and observability requirements

| ID | Requirement | W1 State |
|---|---|---|
| SYS-REL-001 | Startup shall expose deterministic precheck result and fail closed on missing required runtime conditions. | SATISFIED |
| SYS-REL-002 | Hardware-only composition shall remain regression-gated. | SATISFIED |
| SYS-REL-003 | Existing 14 capabilities shall remain regression-protected in every system wave. | REQUIRED |
| SYS-REL-004 | Public consumer contract shall remain schema/regression-gated. | SATISFIED |
| SYS-REL-005 | W2 shall define health/readiness model for Hardware DB, Runtime, Knowledge, source registry and tree state. | W2_INPUT |
| SYS-REL-006 | W2 shall define structured logging/correlation and diagnostic export without leaking secrets/source content. | W2_INPUT |
| SYS-REL-007 | W2 shall define recovery behavior for interrupted tree import, review/publish, and external Knowledge failures. | W2_INPUT |

## 12. Performance and scale requirements

No numeric production SLO is frozen in W1 because the current product baseline does not contain an approved quantitative target.

W2 shall produce measurable budgets for:
- startup/readiness;
- Search and Case Detail latency;
- tree navigation;
- Word intake and AI structuring;
- publish and Knowledge interaction;
- expected case/tree/evidence volume;
- concurrent consumer/maintainer access.

Numeric thresholds must be approved before W3 performance optimization. W3 shall not invent targets.

## 13. Testability and verification requirements

Mandatory system verification:
- Hardware Case Product API E2E.
- Hardware Case focused regression.
- Hardware-only domain boundary.
- Tree Import regression.
- Evidence/Source integrity regression.
- Runtime adapter regression.
- Knowledge adapter regression.
- `hardware-public-consumer/v1` schema and golden-path regression.
- Windows product startup validation.
- macOS/POSIX product startup validation.
- Preserved capability count remains 14/14.

Product test evidence remediation remains test-owned and shall not be treated as a product-code defect unless test evidence proves a real behavior failure.

## 14. W1 gap classification

W1 does not identify a missing P0 product feature.

The remaining system-level work is architecture/engineering hardening:

1. Public Contract deployment binding and version evolution.
2. Cross-platform packaging/launcher parity.
3. DB/schema upgrade + backup/restore boundaries.
4. Production authentication/authorization.
5. Health/readiness/observability/correlation.
6. Recovery/failure-state architecture.
7. Explicit performance/capacity budgets.
8. Configuration and secret lifecycle ownership.

These are W2 architecture inputs, not reasons to reopen the 14 preserved capabilities.

## 15. W2 architecture inputs

W2 shall produce:
- System context and deployment topology.
- Component ownership diagram.
- Runtime/Knowledge/Public Contract dependency architecture.
- Public consumer binding ADR.
- Persistence/schema migration ADR.
- Evidence/source storage boundary.
- Windows/macOS packaging and launcher ADR.
- Configuration/secrets ADR.
- IAM boundary.
- Health/readiness/observability model.
- Failure/recovery matrix.
- Version compatibility strategy.
- W3 implementation backlog derived only from approved architecture gaps.

## 16. Gate

W1_PASS requires:
- System boundary frozen.
- 14/14 preserved capabilities mapped to requirements.
- No duplicate Runtime/Knowledge/Web stack introduced.
- Public consumer contract treated as baseline.
- System-level gaps separated from product-feature gaps.
- W2 inputs explicit.
- No product implementation changed.

W1_RESULT=PASS
READY_FOR_W2_ARCHITECTURE_BASELINE=YES
