# MAJOR-RUNTIME-INTEGRATION-GATE-V1.1

## Result and baseline

This is an integration-preparation gate, not a formal release.  Its only
permitted terminal results are `READY_FOR_D01_E2E` and
`BLOCKED_WITH_DEPENDENCY`.

| field | frozen value |
| --- | --- |
| business base | `fix/req022-release-gate@12ea090f911d376554e6accd3b4ca17386fe937c` |
| runtime base | `main@5f9d0a7093ef1ea2531767d84c05b234eb95335a` (P0.3 accepted) |
| integration source | `integration/major-runtime-v1.1@d39b5a9e320586afb052bfabfc517456a1f1f971` |
| working integration branch | `integration/major-runtime-p03` |
| Agent Config #9 | `feature/agent-config-001@e0bd8c595901d4237d903a387f0a72c35b96a52d` is review-ready only; it is not a `main` release |

`d39b5a9` is a two-parent merge of the business and Runtime bases.  The exact
base merge has no textual conflict.  Do not rebase it on later `main`: that
would import unrelated changes and invalidate this gate's stated baseline.

Affected modules are `runtime/**`, `quality_knowledge/analyzers.py`,
`quality_knowledge/major_cases/**`, `builder/ai_client.py`, and the Runtime,
REQ-022, and legacy-compatibility tests.  The remaining conflict is ownership,
not a Git conflict.

## Frozen ownership boundary (G02 / G05)

Business owns **what** is evaluated: Major Review and Repeat Case input/output
schemas, prompt and skill versions, root-cause/action/repeat judgement,
business merge/dedup/conflict/completeness rules, review decisions, and
business golden data.

Runtime owns **how** it executes: task/run/step/attempt records, retry and
provider-call hard caps, checkpoint/resume/crash recovery, partitioning,
execution snapshots, long-content splitting, partial commits, coverage,
source identity, evidence persistence, and merge orchestration.

The runtime-facing, frozen identifiers are:

| boundary | Major Review D01 | Repeat Case |
| --- | --- | --- |
| `business_domain` | `MAJOR_CASE` | `MAJOR_CASE` |
| `agent_id` | `major_issue.d01.extract` | `major_issue.repeat_case` |
| `workflow_id` | `major_issue_d01_v1` | agent request only |
| request id | supplied by caller; idempotent Runtime request identity | supplied by caller; idempotent Runtime request identity |
| partition key | caller-supplied, isolated in Runtime state and evidence | caller-supplied opaque metadata |
| D01 input | `source`, `expected_objects`, `provider_input` | not applicable |
| D01 output | `MajorIssueD01Outcome` and Runtime status | opaque business result |

`SourceRef`, `SourceBundle`, `LogicalUnit`, `AtomicGroup`,
`LongContentPolicy`, `CoverageUniverse`, `BusinessMerger`, and the business
completeness gate are the contract vocabulary.  `SourceRef` is currently a
Runtime contract.  The latter six are introduced through the P0.3 content
planner/coverage/partial/merge/gate components and must be projected by a
business adapter; business semantics never move into Runtime.

## A / B / C inventory

| class | modules | treatment |
| --- | --- | --- |
| A — Runtime authority | `runtime/engine`, `runtime/store`, `runtime/reliability`, `runtime/content` | Runtime remains the sole task/run/step/attempt, retry, checkpoint, coverage and evidence authority. |
| B — business adapter/projection | `runtime/adapters/major_issue.py`, `runtime/adapters/quality_issue.py` | Preserve business payloads as opaque where required; expose Runtime truth through outcome/projection only. |
| C — legacy compatibility, migration pending | `quality_knowledge/major_cases/skills.py`, `service.py`, `repository.py`, `legacy_adapter.py`, `builder/ai_client.py` | Retain existing behavior for release compatibility.  Do not delete it or label it Runtime-managed until the D01 provider adapter is complete. |

## Gate evidence

| gate | state | executable evidence |
| --- | --- | --- |
| G01 baseline | PASS | `d39b5a9` has both required parents and includes Runtime and REQ-022 tests. |
| G02 boundary | PASS | `MajorIssueD01RuntimeAdapter` and `RepeatCaseRuntimeAdapter` publish the frozen identifiers above; Repeat payload remains opaque. |
| G03 provider boundary | BLOCKED | See `DEP-20260920-002`.  The legacy Major Review real path still creates `OpenAICompatibleClient` with its own retry loop. |
| G04 retry ownership | PARTIAL / BLOCKED | Runtime adapters have explicit one-attempt policies and hard provider budgets.  The actual Major Review real-provider chain cannot yet prove one HTTP request per Runtime attempt. |
| G05 long content | PASS for fixture | D01 fixture commits complete objects individually, resumes only uncovered objects, and applies coverage, evidence, merge and gate checks. |
| G06 snapshot/config | PARTIAL | Runtime snapshots execution definitions.  Agent Config #9 may only be exact-pin input after review; no secret value is stored in this gate. |
| G07 acceptance pack | PASS for mock fixture | Runtime D8, merge blockers, REQ-022 and legacy integration tests cover normal, partial, resume, transport/validation, cap, evidence, partition, opaque Repeat and recovery paths.  Real-provider D01 is blocked by G03. |

## Golden fixtures and acceptance pack (G07)

The golden D01 fixture is synthetic and contains no production material:

* Source: `major://P1/review`, source id `major-doc-P1`, revision `1`,
  fingerprint `fp-P1`.
* Input: four ordered objects `obj-1` through `obj-4`, each with a stable unit
  id and section locator.
* Partial output: `obj-1` to `obj-3` have complete schema-valid evidence;
  `obj-4` is `finish_reason=length` and is not committed.
* Resume output: only `obj-4` is sent to the provider; earlier partial ids and
  execution keys are unchanged.
* Completion evidence: one `EvidenceReference` per object, tied to the source
  and partition key.  Business-consumable is true only after coverage, merge,
  evidence lineage and completeness gate pass.

Required commands, executed from the repository root with the project test
environment, are:

```text
python -m pytest -q tests/test_agent_runtime_p0_d8.py \
  tests/test_agent_runtime_p0_merge_blockers.py \
  tests/test_req022_major_cases.py \
  tests/test_quality_capability_legacy_integration.py
```

The pack covers mock normal execution, truncation to partial, uncovered-only
resume, transport and validation categorisation, provider-call budget,
evidence lineage, partition isolation, opaque Repeat Case, crash/restart
recovery, and business-gate failure.  It does not claim an external real-model
acceptance.

### Executed test record

On the `integration/major-runtime-p03` worktree, the declared test dependency
was installed from `requirements-runtime-p0-test.txt` before execution.

| suite | result |
| --- | --- |
| Runtime P0 D1–D11 and merge blockers | 84 passed |
| REQ-022 Major Case | 21 passed |
| Legacy integration | 9 passed |
| M7/M8 | 53 passed |
| ITRCS material workbench | 14 passed |
| Quality Scenario | 43 passed |
| Workbench | 13 passed |
| cumulative (the two non-overlapping command groups) | 237 passed, 2 warnings |

The Runtime/REQ-022/legacy command group reported 114 passed.  The M7/M8,
ITRCS, Quality Scenario and Workbench command group reported 123 passed.  Test
counts in the rows above may overlap by product label; the command totals are
the authoritative cumulative record.

## Public dependency

### DEP-20260920-002 — Major Review real-provider single-attempt bridge

**Status:** `OPEN` / `PUBLIC_DEPENDENCY`.

**Requirement:** add a D01 business provider adapter that invokes exactly one
HTTP/SDK call for one Runtime Provider Attempt, with `sdk_retry=0`,
`adapter_retry=0`, and `analyzer_retry=0`.  It must classify transport and
validation errors for Runtime without adding a second retry coordinator.

**Acceptance:** an injected counting transport proves `N` Runtime provider
attempts produce exactly `N` requests; Runtime alone records the retry budget,
partial result, checkpoint and resume.  The existing legacy non-Runtime path
must retain its release-compatible behavior until cutover.

## Release status and next step

* Windows BAT: `UNVERIFIED`.
* External real model: `UNVERIFIED`.
* Manual de-identification business review: `UNVERIFIED`.
* Formal release: `NOT APPROVED`.

Next: implement and review DEP-20260920-002, then execute the D01 real
business E2E with approved de-identified material and a separately recorded
configuration snapshot.
