# W3 多案例有界并发架构设计 V0.1

任务：HARDWARE-W3-MULTI-AGENT-CONCURRENCY-001

基线：5957cf3477152245ec7daf69821509612e0f4979
分支：feature/hardware-w3-multi-agent-concurrency
Issue：https://github.com/xm286947-cell/ploblem_ai/issues/554

# HARDWARE-W3-MULTI-AGENT-CONCURRENCY-001 (P0)

Owner: Hardware Case system architect / R&D lead
Source baseline: `5957cf3477152245ec7daf69821509612e0f4979`
Development branch: `feature/hardware-w3-multi-agent-concurrency`

## Scope
Support concurrent **different hardware cases**, never concurrent Stage A and Stage B for the same case. Stage B consumes validated Stage A, with unchanged Prompt/Schema/Cache/Knowledge and explicit Human Review/Publish gate.

## W3-00 Code fact audit
- `services/hardware_case_r1_workbench.py`: W2 `run_batch` iterates queued items sequentially; `_run_item` owns input identity gate -> RUNNING -> per-item factory -> pipeline -> candidate durable commit -> status. `retry_failed_only` and per-case retry exist.
- `services/hardware_case_markdown_agent.py`: the V1.3 pipeline enforces Stage A -> validate -> cache commit -> Stage B -> validate, unchanged.
- `services/hardware_case_r1_runtime.py`: per-item pipeline factory creates Stage A/B runners; `_R1StageCache` uses scoped SQLite connections. Runtime tasks use `SqliteTaskStore`, WAL/busy_timeout=30s.
- `services/hardware_asset_repository.py`: Candidate repository uses independent SQLite connections and `BEGIN IMMEDIATE` for write transactions; deterministic Candidate identity.
- `services/hardware_case_r1_workbench.py`: Workbench Store uses independent sqlite3 connections per call; introduced atomic compare-and-set claim on Item status/revision before Provider work.
- Shared Unified Runtime already has parallel DAG support; it must not be rewritten. Case parallelism belongs in Batch orchestration and does not require a DAG across independent case runs.

## W3-01 Minimal implementation (submitted, not yet accepted)
- One process-shared `ThreadPoolExecutor(max_workers=4)`; per-Batch submission window `1..4`, parallel default `2`, sequential remains W2 default.
- API opt-in `POST /api/v2/hardware-cases/r1/workbench/batches/{batch_id}/run-resume?execution_mode=PARALLEL&concurrency=2`.
- No extra Provider configuration or Runtime/Agent schema changes; each item still constructs its own pipeline and commits only its own Candidate.
- Item `RUNNING` transition guarded by atomic SQLite CAS (`item_id`, previous `orchestration_status`, `updated_at`). Stale requests skip Provider work.
- Worker exceptions are persisted as `RUNTIME_BLOCKED` via existing W2 failure vocabulary; exceptions persisting failure must still fail closed.

## Explicit known limits / W3-02 blockers
- Process-wide cap does **not** yet span separate server processes/hosts. Do not claim deployment-wide limit in multiprocess deployments; introduce a reviewed SQLite lease / shared capacity gate or enforce a single worker process.
- Run/Resume/Cancel across crashes, source-ID aliasing across Batches, stage cache write collisions, Provider budget and timeouts, forced process kill and transaction stress need dedicated acceptance. W3-01 must not claim these gates complete.
- Current UI has not yet received mode selector/progress/cancel controls (W3-03).
- W2 full CI contains two legacy Word Import red checks; this scope must not relabel Release green.

## Acceptance and execution sequence
W3-00 => code audit/design; W3-01 => mock-only overlap, per-Batch cap, cross-Batch process cap, sequential fallback, per-item failure isolation, atomic duplicate claim. W3-02 => recovery/cancellation/idempotency/SQLite stress. W3-03 => existing UI incremental integration. W3-04 => Mock CI, isolated real-provider test, Windows/macOS Fresh Extract, W2 regressions, internal Candidate.

**Freeze:** no Formal Knowledge schema mutation, auto write/publish, Stage A/B semantic or prompt modification, new Runtime/Provider, or overwrite of original data/installation. Real Provider must never run in CI.

