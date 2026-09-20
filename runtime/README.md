# Unified Agent Runtime — P0.3

## Frozen engineering baseline

- Repository: `xm286947-cell/ploblem_ai`
- Source branch: `main`
- Base commit: `ec071ccf7ac5133b9ebd25e1b55dba47cc7e808f`
- Work branch: `feature/agent-runtime-p0`
- Contract baseline: `P0.2_CONTRACT_FROZEN_V1.0`
- Acceptance baseline: `P0.3_RUNTIME_ACCEPTANCE_MATRIX_V0.2`
- Development plan: `RUNTIME_DEVELOPMENT_OBJECTIVES_AND_PLAN_V1.0_LOCKED`

## Current implementation boundary

This branch is implementing the first P0.3 batch in the locked order:

1. D0 — engineering baseline lock.
2. D1 — canonical contract skeleton + persistent SQLite TaskStore.
3. D2 — minimal execution core.

Implemented in D1/D2:

- Canonical `AgentRequest / AgentResult`.
- Canonical `WorkflowRequest / WorkflowResult`.
- `Task / Run / StepRun / Attempt / Checkpoint` contract records.
- Persistent SQLite `TaskStore`.
- `invoke / execute / submit / get_task`.
- `SINGLE / SEQUENTIAL / PARALLEL` execution modes.
- `LightweightExecutionEngine`.
- Generic D1/D2 tests, including SQLite reopen/read persistence.

## Explicitly not claimed yet

The following belong to subsequent locked stages and are not considered complete in D1/D2:

- D3: request idempotency, stable execution keys, Retry Budget, atomic commit, checkpoint/resume, crash recovery.
- D4: long-content strategy/planning/chunking.
- D5: coverage/evidence/merge/completeness.
- D6: immutable execution snapshot, partition isolation, full cooperative cancellation.
- D7-D9: business fixtures/adapters.
- D10: LangGraph engine comparison.
- D11: full P0.3 acceptance.

No P0.2 frozen contract semantics may be silently changed during implementation.


## D3 reliability evidence

- Request idempotency and fingerprint conflict.
- Stable execution keys and provider-call hard budgets.
- Atomic result/checkpoint/attempt commit marker.
- Same Task + new Run resume.
- Crash recovery at before-call, after-return-before-commit, and after-commit-before-status windows.
- PR #3 CI evidence: 12 tests passed before D4 entry.

## D4 implementation

- Explicit ContentStrategyRegistry / ContentProjector boundary.
- SourceBundle / LogicalUnit / AtomicGroup contracts.
- Deterministic ContentPlanner.
- KEEP_TOGETHER / SAME_CONTEXT enforcement.
- Chunk capacity and overlap planning.
- PartialResultCommitter rejects schema-invalid or incomplete/truncated objects.

D5 Coverage / Evidence / Merge remains intentionally outside the D4 implementation boundary.
