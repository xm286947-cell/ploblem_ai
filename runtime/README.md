# Unified Agent Runtime — P0.3

## Baseline

- Repository: `xm286947-cell/ploblem_ai`
- Source branch: `main`
- P0.3 base commit: `ec071ccf7ac5133b9ebd25e1b55dba47cc7e808f`
- Work branch: `feature/agent-runtime-p0`
- Contract baseline: `P0.2_CONTRACT_FROZEN_V1.0`
- Validated contract state: `P0.2_CONTRACT_VALIDATED_V1.0`
- Acceptance baseline: `P0.3_RUNTIME_ACCEPTANCE_MATRIX_V0.2`

## P0.3 status

D0-D11 implementation and mandatory acceptance are complete on the P0.3 branch.

Validated scope:

- Canonical `AgentRequest / AgentResult / WorkflowRequest / WorkflowResult`.
- Persistent `Task / Run / StepRun / Attempt / Checkpoint` state.
- `SINGLE / SEQUENTIAL / PARALLEL` execution.
- Request idempotency and stable `execution_key`.
- Runtime-owned Retry Budget and provider-call hard caps.
- Atomic commit, checkpoint, resume, and crash recovery.
- Long-content projection, planning, AtomicGroup, and partial commits.
- Coverage, Source Identity, Evidence, Merge, and Completeness Gate.
- Immutable Execution Definition Snapshot.
- Partition isolation.
- Cooperative cancellation.
- Legacy Quality Issue adapter and projection outbox.
- MajorIssue D01 compatibility fixture.
- Storage compatibility fixture.
- Engine replacement comparison.

P0 default engine:

- `LightweightExecutionEngine`

Validated alternative:

- `LangGraphExecutionEngine`

LangGraph is not a default production dependency. It is declared in
`requirements-runtime-p0-test.txt` for D10 engine-comparison and P0 acceptance
reproducibility.

## Retry ownership

For Runtime-managed adapters, Runtime is the single retry authority.

- Provider/SDK hidden transport retry is disabled.
- Legacy analyzer hidden validation retry is disabled.
- Transport and validation failures are surfaced as categorized Runtime errors.
- Every real provider request consumes Runtime provider-call budget.

Legacy non-Runtime call paths retain their existing behavior.

## Acceptance

The pre-review P0.3 gate completed:

- Mandatory Cases: 64 / 64 PASS
- Final D1-D11 CI: 80 passed in 4.40s

Final merge review then identified two engineering closure blockers:

1. repository-declared LangGraph test dependency;
2. Runtime-owned retry semantics on the real Legacy Quality Issue chain.

Both are covered by `tests/test_agent_runtime_p0_merge_blockers.py` and must pass
together with the original D1-D11 suite before merging to `main`.

## Not claimed by P0.3

P0.3 validation does **not** mean:

- real business data acceptance is complete;
- real-model quality/cost evaluation is complete;
- Windows/PVE/production deployment is accepted;
- all business domains are migrated;
- distributed Worker/Queue/HA is implemented.

Do not silently change frozen P0.2 Contract semantics during subsequent work.
