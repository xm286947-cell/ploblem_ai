# Major Issue → Unified Agent Runtime Integration Gate V1.1

Status: **BLOCKED**
Date: 2026-09-21
Business domain: `MAJOR_CASE`

## 1. Baselines

- Major Issue product baseline:
  - `fix/req022-release-gate@12ea090f911d376554e6accd3b4ca17386fe937c`
- Unified Agent Runtime current main:
  - `main@1d1ab5fc7050f63b5ba18ce4902c4b658b49fd1f`
- P0.3 original release commit:
  - `5f9d0a7093ef1ea2531767d84c05b234eb95335a`
- Agent Config PR #9: merged
- Runtime secret hardening PR #14: merged
- Storage × Runtime acceptance PR #15: merged
- Integration branch:
  - `integration/major-runtime-v1.1`
- Initial P0.3 alignment merge commit:
  - `d39b5a9e320586afb052bfabfc517456a1f1f971`
- Latest-main alignment merge commit:
  - `7763521f17a9b5fc48bbf5e66b40064047523711`
- Alignment result:
  - versus Runtime main: behind = 0
  - versus REQ-022 release-gate: behind = 0
  - changed-file overlap before merge: 0

No Runtime architecture redesign was introduced.

## 2. Integration mode

Primary mode: **Domain Adapter + Canonical Workflow**

Legacy compatibility:
- existing major-case Repository / Web / Review remain business-owned;
- old `kb_run/kb_step` execution state is not the future Runtime source of truth;
- after cutover, legacy execution state must become compatibility projection / business audit only.

Repeat Case:
- retrieval / ranking / threshold / repeat judgement remain major-case business semantics;
- Runtime treats the Repeat Case payload as opaque and only owns reliable execution.

## 3. Canonical Contract

### D01 extraction

- business_domain: `MAJOR_CASE`
- agent_id: `major_issue.d01.extract`
- workflow_id: `major_issue_d01_v1`
- output: `MajorIssueD01Outcome`
- required business objects:
  - ISSUE_FACT
  - ROOT_CAUSE
  - ACTION
  - VERIFICATION
- partition_key: `event_id`
- request_id:
  - deterministic hash of case/version/event/source identity/skill version.

Implementation:
- `quality_knowledge/major_cases/runtime_integration.py`

### Repeat Case

- agent_id: `major_issue.repeat_case`
- business payload remains opaque to Runtime.

## 4. Long Content / Domain Strategy

Business mapping is frozen as follows:

- SourceRef:
  - one immutable major-review document version;
  - fingerprint contains document/version/content/parser identity.
- LogicalUnit:
  - `kb_fragment`.
- AtomicGroup:
  - fragments in the same business section use `SAME_CONTEXT`.
- Partition:
  - every LogicalUnit uses the owning `event_id`;
  - cross-case event binding is rejected.
- Coverage Universe:
  - all fragments of one document version within one event partition.
- Business Merge:
  - key = entry_type;
  - dedup uses business object + evidence identity;
  - conflicting evidence is preserved for human review, not silently overwritten.
- Business Completeness / Review Gate:
  - no PENDING/MISSING entry;
  - no confirmed UNSCOPED knowledge in multi-event cases;
  - only after the business gate passes may the result become business-consumable.

## 5. Provider / Retry boundary

Frozen rule:

> one Runtime Provider Attempt = one real Provider HTTP/SDK request.

Required:
- Runtime is the only Retry Owner.
- sdk_retry = 0.
- adapter_retry = 0.
- analyzer_retry = 0.
- actual provider requests == Runtime provider_calls.
- Provider transport errors map to Runtime-standard categories.
- Secret values must not enter Snapshot / Log / Error / Checkpoint.

Current existing major-case `builder/OpenAICompatibleClient` has its own retry loop.
It therefore **must not be used as the final Runtime-managed Provider path**.

Current main also contains a Storage-specific single-call real-provider adapter used by Storage E2E. It validates the required boundary pattern, but it is domain-specific and does not close the generic ORCH-B01 requirement for Major Issue.

Public dependency:
- GitHub Issue #11
- `ORCH-B01 — Runtime Provider Execution Adapter`

No business-side workaround will be added.

## 6. G01–G07 result

| Gate | Result | Evidence / conclusion |
|---|---|---|
| G01 Baseline | PASS | integration branch contains current main `1d1ab5fc...` and REQ-022 release-gate; both compare results are behind=0 |
| G02 Contract | PASS | canonical IDs, input/output semantics, deterministic request_id and event partition are frozen in runtime_integration.py |
| G03 Provider Boundary | **BLOCKED** | ORCH-B01 / GitHub Issue #11; current legacy OpenAICompatibleClient has internal retry |
| G04 Retry Ownership | PASS | contract freezes Runtime-only retry; Runtime D11 retry/hard-budget acceptance is included in integration CI |
| G05 Long Content | PASS | SourceRef / LogicalUnit / AtomicGroup / Coverage / Business Merge / Business Gate defined and acceptance-tested |
| G06 Snapshot / Secret | PASS | Agent Config and secret-hardening are now merged to main; business adapter contains no secret; Runtime config redacts secret-bearing success/error surfaces |
| G07 Acceptance | PASS | Runtime D8 + Domain Gate + Runtime D11 + REQ-022 regression all pass |

Overall Integration Gate: **BLOCKED by G03 only**.

Runtime Core new blocker: **0**.

## 7. Automated evidence

Latest completed integration run before latest-main re-alignment:

- GitHub Actions Run: `35521969799`
- Result: SUCCESS
- Runtime D8 major-issue fixture: **5 passed**
- Major-case Domain Integration Contract: **6 passed**
- Runtime retry / hard-budget acceptance: **20 passed**
- REQ-022 regression: **21 passed, 1 warning**

The warning is the existing Starlette / AnyIO deprecation warning and is not a functional failure.

Integration workflow:
- `.github/workflows/major-runtime-integration-gate.yml`

Acceptance cases:
- `tests/test_major_runtime_integration_gate.py`
- `tests/test_agent_runtime_p0_d8.py`
- `tests/test_agent_runtime_p0_d11_acceptance_gaps.py`
- `tests/test_req022_major_cases.py`

## 8. A / B / C classification

### A — ready / reusable

- major-case object schema and evidence model;
- Event / ITR business isolation;
- human Review and Revision;
- Repeat Case business semantics;
- Unified Runtime P0.3 Task/Run/Retry/Resume/Checkpoint/Coverage/Evidence/Partition;
- Runtime MajorIssue D8 fixture.

### B — Major Issue project work

- continue using `MajorCaseRuntimeDomainAdapter`;
- wire the actual D01 business handler to the canonical Runtime workflow after G03 is released;
- demote old business execution-state objects to Projection / audit role;
- execute D01 real E2E;
- then connect Repeat Case E2E;
- complete business Golden acceptance.

### C — public dependency

- ORCH-B01 / Issue #11 Runtime Provider Execution Adapter.
- Agent Config PR #9 is now merged to main and is part of the current public baseline.
- The only public dependency blocking this Gate is ORCH-B01 / Issue #11.

## 9. Entry condition for D01 real E2E

D01 real-model E2E may start only when ORCH-B01 proves:

1. OpenAI-compatible real invocation is Runtime-owned.
2. One Runtime attempt produces exactly one real Provider request.
3. Hidden SDK / adapter retry is disabled.
4. actual provider requests == Runtime provider_calls.
5. transport errors map into Runtime standard errors.
6. Secret values are absent from Runtime persistence/evidence/error surfaces.

Until then:
- do not attribute real-model failures to model quality;
- do not add a business-side Provider retry loop;
- Integration Gate remains BLOCKED.

## 10. Non-release statement

This Gate does not approve product release.

Still UNVERIFIED:
- Windows BAT target-environment validation;
- external real-model E2E;
- manual de-identified business validation.

Formal Release remains **NOT APPROVED**.


## 11. Historical P0.3 local pre-acceptance evidence

This section preserves the earlier local P0.3 gate evidence for audit only.
It does **not** override the current G01–G07 status above.

Frozen evidence branch:
- `integration/major-runtime-p03@9a0e5f7d70e708ddadc54992f7ddf60ce58d9d00`
- base business: `fix/req022-release-gate@12ea090f911d376554e6accd3b4ca17386fe937c`
- base Runtime: `main@5f9d0a7093ef1ea2531767d84c05b234eb95335a`
- role: old P0.3 local Integration Gate pre-acceptance evidence
- status: frozen; no further development; no merge/rebase into current integration branch

Executed local test record on that frozen branch:
- Runtime P0 D1–D11 + merge blockers: **84 passed**
- REQ-022 Major Case: **21 passed**
- Legacy integration: **9 passed**
- M7/M8: **53 passed**
- ITR/CS: **14 passed**
- Quality Scenario: **43 passed**
- Workbench: **13 passed**
- cumulative authoritative command total: **237 passed, 2 known deprecation warnings**

The older branch also documented a synthetic D01 Golden fixture covering:
- schema-valid partial object commits;
- truncated final object not being committed;
- uncovered-only resume;
- stable prior partial/execution identities;
- one EvidenceReference per object;
- business consumption only after coverage, merge, evidence lineage and completeness gate pass.

Historical interpretation:
- G03 Provider Boundary was already identified as the blocking dependency.
- G04 and G06 were only partial on the old P0.3 baseline.
- those two items are superseded by the current branch evidence:
  - G04 is now PASS through Runtime D11 retry/hard-budget acceptance.
  - G06 is now PASS after Agent Config and secret-hardening were merged to main.
- therefore this appendix is evidence lineage only, not the current Gate decision.
