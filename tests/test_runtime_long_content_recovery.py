from __future__ import annotations

from collections import defaultdict

from runtime import (
    AtomicGroup,
    AtomicGroupPolicy,
    ContentSource,
    ErrorCategory,
    ExecutionPolicy,
    LogicalUnit,
    LongContentPolicy,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    RuntimeStepError,
    SourceBundle,
    SourceRef,
    SqliteTaskStore,
)
from runtime.content import LongContentRecoveryExecutor
from runtime.engine import LightweightExecutionEngine


def _source() -> SourceRef:
    return SourceRef(
        source_id="doc-1",
        source_type="TEST_DOCUMENT",
        revision="1",
        content_hash="sha256-doc-1",
        fingerprint="fp-doc-1",
        uri="memory://doc-1",
    )


def _bundle(
    count: int,
    *,
    atomic_groups: list[AtomicGroup] | None = None,
) -> SourceBundle:
    src = _source()
    return SourceBundle(
        bundle_id=f"bundle-{count}",
        sources=[
            ContentSource(
                source=src,
                inline_content={"kind": "test"},
                partition_key="P1",
            )
        ],
        logical_units=[
            LogicalUnit(
                unit_id=f"u{i}",
                source_id=src.source_id,
                locator={"index": i},
                inline_payload={"text": f"unit-{i}"},
                partition_key="P1",
                metadata={"estimated_payload_chars": 100},
            )
            for i in range(1, count + 1)
        ],
        atomic_groups=list(atomic_groups or []),
        default_partition_key="P1",
        shared_context={},
    )


def _policy(
    *,
    validation_attempts: int,
    per_step_budget: int,
    task_budget: int | None = None,
) -> ExecutionPolicy:
    return ExecutionPolicy(
        validation_retry=RetryPolicy(
            max_attempts=validation_attempts,
        ),
        transport_retry=RetryPolicy(max_attempts=1),
        step_retry=RetryPolicy(max_attempts=1),
        retry_budget=RetryBudget(
            max_provider_calls_per_step=per_step_budget,
            max_provider_calls_per_task=task_budget,
            max_step_attempts=1,
            max_validation_cycles_per_step_attempt=validation_attempts,
            max_transport_attempts_per_model_call=1,
        ),
    )


def _chunk_units(payload) -> tuple[str, ...]:
    return tuple(payload["chunk"]["unit_ids"])


def test_orch_b02_truncation_retries_only_affected_chunk_then_merges(
    tmp_path,
) -> None:
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls: dict[tuple[str, ...], int] = defaultdict(int)

    def provider(payload, context):
        key = _chunk_units(payload)
        calls[key] += 1
        if key == ("u1", "u2") and calls[key] == 1:
            raise RuntimeStepError(
                "truncated",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={"finish_reason": "length"},
            )
        return {
            "unit_ids": list(key),
            "provider_call_seq": context["runtime"]["provider_call_seq"],
        }

    executor = LongContentRecoveryExecutor(
        runtime,
        store,
        step_execution_policy=_policy(
            validation_attempts=2,
            per_step_budget=2,
            task_budget=4,
        ),
    )
    executor.bind_agent(
        agent_id="test.long_content",
        provider_handler=provider,
        execution_policy=executor.step_execution_policy,
    )

    outcome = executor.execute(
        _bundle(4),
        request_id="orch-b02-retry-merge",
        policy=LongContentPolicy(
            max_units_per_chunk=2,
            max_payload_chars=1000,
        ),
        strategy_ref="test@1",
    )

    assert outcome.status == RuntimeStatus.COMPLETED
    assert outcome.runtime_status == RuntimeStatus.COMPLETED
    assert outcome.provider_calls == 3
    assert calls == {
        ("u1", "u2"): 2,
        ("u3", "u4"): 1,
    }
    assert len(outcome.committed_partials) == 2
    assert {item.chunk_id for item in outcome.committed_partials}
    assert all(item.metadata["finish_reason"] == "stop" for item in outcome.committed_partials)
    assert all(item.complete for item in outcome.coverages)
    assert outcome.merge is not None
    assert outcome.merge.complete is True
    assert outcome.gate.passed is True
    assert outcome.business_consumable is True

    # The truncated attempt never became a committed partial.
    assert len(store.list_partial_results(outcome.task_id)) == 2


def test_orch_b02_resume_reuses_completed_chunk_and_continues_unfinished(
    tmp_path,
) -> None:
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls: list[tuple[str, ...]] = []
    allow_second = {"value": False}

    def provider(payload, context):
        key = _chunk_units(payload)
        calls.append(key)
        if key == ("u2",) and not allow_second["value"]:
            raise RuntimeStepError(
                "truncated",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={"finish_reason": "length"},
            )
        return {"unit_ids": list(key)}

    executor = LongContentRecoveryExecutor(
        runtime,
        store,
        step_execution_policy=_policy(
            validation_attempts=1,
            per_step_budget=3,
            task_budget=6,
        ),
    )
    executor.bind_agent(
        agent_id="test.resume",
        provider_handler=provider,
        execution_policy=executor.step_execution_policy,
    )

    first = executor.execute(
        _bundle(3),
        request_id="orch-b02-resume",
        policy=LongContentPolicy(
            max_units_per_chunk=1,
            max_payload_chars=1000,
        ),
        strategy_ref="test@1",
    )

    assert first.status == RuntimeStatus.PARTIAL
    assert first.runtime_status == RuntimeStatus.PARTIAL
    assert first.provider_calls == 2
    assert calls == [("u1",), ("u2",)]
    assert [item.unit_ids for item in first.committed_partials] == [["u1"]]
    assert first.merge is None
    assert first.business_consumable is False

    allow_second["value"] = True
    resumed = executor.resume(first.task_id)

    assert resumed.status == RuntimeStatus.COMPLETED
    assert resumed.provider_calls == 4
    # u1 is a committed Runtime step and must not trigger another provider call.
    assert calls == [
        ("u1",),
        ("u2",),
        ("u2",),
        ("u3",),
    ]
    assert [item.unit_ids for item in resumed.committed_partials] == [
        ["u1"],
        ["u2"],
        ["u3"],
    ]
    assert resumed.merge is not None and resumed.merge.complete is True
    assert resumed.gate.passed is True
    assert resumed.business_consumable is True

    runs = store.list_runs(first.task_id)
    assert len(runs) == 2
    assert runs[1].resume_of_run_id == runs[0].run_id


def test_orch_b02_task_budget_stops_requests_but_keeps_valid_partial(
    tmp_path,
) -> None:
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls: list[tuple[str, ...]] = []

    def provider(payload, context):
        key = _chunk_units(payload)
        calls.append(key)
        if key == ("u2",):
            raise RuntimeStepError(
                "truncated",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={"finish_reason": "length"},
            )
        return {"unit_ids": list(key)}

    executor = LongContentRecoveryExecutor(
        runtime,
        store,
        step_execution_policy=_policy(
            validation_attempts=2,
            per_step_budget=2,
            task_budget=2,
        ),
    )
    executor.bind_agent(
        agent_id="test.budget",
        provider_handler=provider,
        execution_policy=executor.step_execution_policy,
    )

    outcome = executor.execute(
        _bundle(3),
        request_id="orch-b02-budget",
        policy=LongContentPolicy(
            max_units_per_chunk=1,
            max_payload_chars=1000,
        ),
        strategy_ref="test@1",
        max_provider_calls_per_task=2,
    )

    assert outcome.status == RuntimeStatus.PARTIAL
    assert outcome.provider_calls == 2
    assert calls == [("u1",), ("u2",)]
    assert [item.unit_ids for item in outcome.committed_partials] == [["u1"]]
    assert outcome.merge is None
    assert outcome.gate.passed is False
    assert outcome.business_consumable is False


def test_orch_b02_planner_preserves_business_atomic_group(
    tmp_path,
) -> None:
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls: list[tuple[str, ...]] = []

    def provider(payload, context):
        key = _chunk_units(payload)
        calls.append(key)
        return {"unit_ids": list(key)}

    executor = LongContentRecoveryExecutor(
        runtime,
        store,
        step_execution_policy=_policy(
            validation_attempts=1,
            per_step_budget=1,
            task_budget=4,
        ),
    )
    executor.bind_agent(
        agent_id="test.atomic",
        provider_handler=provider,
        execution_policy=executor.step_execution_policy,
    )

    bundle = _bundle(
        4,
        atomic_groups=[
            AtomicGroup(
                group_id="g23",
                unit_ids=["u2", "u3"],
                policy=AtomicGroupPolicy.KEEP_TOGETHER,
            )
        ],
    )
    outcome = executor.execute(
        bundle,
        request_id="orch-b02-atomic",
        policy=LongContentPolicy(
            max_units_per_chunk=2,
            max_payload_chars=1000,
        ),
        strategy_ref="business-grouping@1",
    )

    assert outcome.status == RuntimeStatus.COMPLETED
    assert ("u2", "u3") in calls
    assert not any(
        ("u2" in item) ^ ("u3" in item)
        for item in calls
    )
    assert outcome.gate.passed is True


def test_orch_b02_final_business_gate_can_block_consumption_after_merge(
    tmp_path,
) -> None:
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)

    def provider(payload, context):
        return {"unit_ids": list(_chunk_units(payload))}

    executor = LongContentRecoveryExecutor(
        runtime,
        store,
        business_gate=lambda merge, coverages, bundle, plan: False,
        step_execution_policy=_policy(
            validation_attempts=1,
            per_step_budget=1,
            task_budget=2,
        ),
    )
    executor.bind_agent(
        agent_id="test.business_gate",
        provider_handler=provider,
        execution_policy=executor.step_execution_policy,
    )

    outcome = executor.execute(
        _bundle(2),
        request_id="orch-b02-business-gate",
        policy=LongContentPolicy(
            max_units_per_chunk=2,
            max_payload_chars=1000,
        ),
        strategy_ref="test@1",
    )

    assert outcome.runtime_status == RuntimeStatus.COMPLETED
    assert outcome.status == RuntimeStatus.PARTIAL
    assert outcome.merge is not None and outcome.merge.complete is True
    assert outcome.gate.business_gate_passed is False
    assert "BUSINESS_GATE_FAILED" in outcome.gate.reasons
    assert outcome.business_consumable is False
