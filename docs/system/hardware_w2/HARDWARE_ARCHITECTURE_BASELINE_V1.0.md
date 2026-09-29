# Hardware Architecture Baseline V1.0

TASK=HARDWARE-ARCHITECTURE-BASELINE-W2-001
STATUS=HARDWARE_ARCHITECTURE_BASELINE_V1_FROZEN
BASE_MAIN=efa697fd341759f0c41887ab89fd2cf36e204081
PRODUCT_FEATURE_CHANGE=NO
PRESERVED_CAPABILITIES=14/14

## Frozen ownership
- Hardware Domain = OWNED_BY_HARDWARE
- Hardware DB = PRIVATE_IMPLEMENTATION
- hardware-public-consumer/v1 = ONLY_PUBLIC_CONSUMER_BOUNDARY
- Unified Runtime = REUSE / NOT_OWNED
- Unified Knowledge = REUSE / NOT_OWNED
- Overall = CONSUMER_ONLY
- SECOND_RUNTIME = FORBIDDEN
- SECOND_KNOWLEDGE_PLATFORM = FORBIDDEN
- CROSS_DOMAIN_SQL = FORBIDDEN
- PRESERVED_14_CAPABILITIES = DO_NOT_REDESIGN

## System context
```text
User / Maintainer
      |
      v
Shared FastAPI Host
      |
      +--> Hardware UI + Hardware API
      |       |
      |       +--> Hardware Domain Services
      |       |       |
      |       |       +--> Hardware Private SQLite
      |       |       +--> Company-local Source Registry
      |       |       +--> Runtime Adapter --> Unified Runtime
      |       |       +--> Knowledge Adapter --> Unified Knowledge
      |       |
      |       +--> hardware-public-consumer/v1 facade
      |
      +--> Overall / future consumers
              consume public Hardware boundary only
```

## A01 Public Contract deployment + versioning
- External cross-product HTTP namespace: `/api/public/hardware/v1`.
- It is a thin facade over existing Hardware consumer projection; no duplicate business logic or persistence.
- Existing `/api/v2/hardware-cases` remains Hardware product API and is not the cross-domain contract.
- v1 is additive-only: optional fields may be added, required fields/meaning may not change.
- Breaking changes require `/v2`.
- When v2 is introduced, v1 must remain supported for at least one subsequent Hardware release and emit explicit deprecation metadata before removal.
- Consumers may deep-link to Hardware-owned UI but must not import repositories/services/private DB.

## A02 Component boundary
Hardware owns Web routes, domain services, Hardware DB, source registry, tree import, public facade and adapters.
Runtime owns provider/retry/checkpoint/secret execution semantics.
Knowledge owns Candidate/Evidence/Review/Publish/Object/Query mechanics.
Overall owns only presentation/integration composition.
No shared database joins or cross-domain SQL are permitted.

## A03 Windows/macOS release architecture
- One release artifact contains one Python implementation and both OS launch wrappers.
- Windows: `START_HARDWARE_CASE.bat`.
- macOS/POSIX: `START_HARDWARE_CASE.sh`; W3.1 adds thin Finder-friendly `START_HARDWARE_CASE.command` delegating to the same shell path.
- Business logic, precheck, config resolution and app factory remain common Python code.
- Package manifest records source commit, contract versions, launcher inventory and hashes.
- Windows/macOS release gates must prove same routes, DB semantics, public contract, Runtime/Knowledge adapters and 14/14 preservation.

## A04 Data reliability
- Hardware DB carries an explicit monotonic schema version.
- Migrations are ordered, transactional and forward-only.
- Before a schema-changing migration, create a verified pre-migration backup.
- Rollback is restore-based, not reverse-DDL based.
- Backup set contains Hardware SQLite + source-root manifest/checksums; source files remain company-local.
- Restore verifies DB integrity, schema version and source hashes before activation.
- Failed/pending migration blocks startup and does not partially serve traffic.

## A05 IAM
- CONSUMER and MAINTAINER remain business roles.
- Production identity is resolved by host/platform auth; request headers are only an internal-test compatibility mechanism.
- Public read boundary is CONSUMER-only.
- Mutations, source upload, review, mapping, tree import and publish require MAINTAINER.
- Authorization decision occurs at API boundary; domain service still validates operation invariants.
- No anonymous path may escalate to MAINTAINER.

## A06 Health / Readiness
Expose Hardware health with dependency states:
- HARDWARE_DB
- SOURCE_REGISTRY
- ACTIVE_TREE_STATE
- UNIFIED_RUNTIME_CONFIG
- UNIFIED_KNOWLEDGE
- PUBLIC_CONTRACT
Readiness is fail-closed for mandatory dependencies required by the requested operation.
Liveness is process-local and must not depend on remote provider availability.

## A07 Observability / Correlation
A single `correlation_id` flows across Intake -> Runtime -> Review -> Publish -> Knowledge.
Structured events must include operation, case_id/intake_id when safe, component, result, error_code, duration and correlation_id.
Never log secrets, Authorization, raw provider payloads or source document content.
Diagnostic export is metadata-only and safe for support transfer.

## A08 Failure / Recovery / Idempotency
Failures are classified as VALIDATION, DATA_INTEGRITY, DEPENDENCY_UNAVAILABLE, AUTHORIZATION, CONFLICT, TRANSIENT, INTERNAL.
Retry is allowed only for idempotent/transient operations.
Tree Apply remains atomic.
Publish uses stable idempotency keys already defined by the Knowledge adapter.
Interrupted migration fails closed.
Interrupted external Knowledge publish is reconciled by public ref/idempotency key, never by duplicate blind publish.
Last valid confirmed/published state is not overwritten by failed AI/runtime attempts.

## A09 Config / Secret lifecycle
Precedence:
1. explicit process environment / secret references;
2. approved local config;
3. packaged non-secret defaults.
Secrets must never be committed, bundled, logged or persisted in Hardware DB.
Hardware owns config references; Runtime owns provider secret consumption.
Startup precheck reports missing references by code only.
Rotation must not require code change.

## A10 Performance / Capacity budget
These are engineering gates, not customer SLA:
- local startup/readiness: <= 30 s excluding first-time dependency installation;
- consumer Search/Detail/Tree public reads: p95 <= 1.0 s on reference local dataset;
- local review/mapping/publish-gate operations excluding external dependencies: p95 <= 2.0 s;
- Runtime structuring remains bounded by existing 180 s agent timeout;
- reference W3 capacity envelope: 10,000 cases, 100,000 evidence records, 50,000 tree nodes, 10 concurrent interactive users;
- no W3 change may regress focused read latency by >20% from captured baseline without architecture review.

## W3 packages
W3.1 RELEASE_AND_OPERABILITY:
public binding/version compatibility, dual-platform release parity, health/readiness, config/secret lifecycle.

W3.2 DATA_RELIABILITY:
schema version, migration, pre-migration backup, restore, rollback/fail-closed.

W3.3 PRODUCTION_RELIABILITY:
IAM, correlation, structured observability, failure classification, recovery/idempotency, performance/capacity gate.

## Architecture gate
ADR_ACCEPTED=9/9
OPEN_ARCH_BLOCKER=0
READY_FOR_W3=YES
