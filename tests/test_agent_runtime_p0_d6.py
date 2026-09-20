from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime import (
    AgentDefinition,
    AgentRequest,
    ErrorCategory,
    ExecutionMode,
    ExecutionPolicy,
    LightweightExecutionEngine,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    RuntimeStepError,
    SqliteTaskStore,
    StepDefinition,
    TaskRecord,
    TaskType,
    WorkflowDefinition,
    WorkflowRequest,
)
from runtime.content import (
    CoverageCalculator,
    EvidenceRegistry,
    ListResultMerger,
    PartitionMismatchError,
)
from runtime.contracts import (
    CommittedPartialResult,
    CoverageUnit,
    CoverageUniverse,
    EvidenceLocator,
    EvidenceReference,
    MergeContext,
    SourceRef,
)


def policy(*, model_policy=None):
    return ExecutionPolicy(
        mode=ExecutionMode.SEQUENTIAL,
        transport_retry=RetryPolicy(max_attempts=1),
        validation_retry=RetryPolicy(max_attempts=1),
        step_retry=RetryPolicy(max_attempts=1),
        retry_budget=RetryBudget(
            max_provider_calls_per_step=2,
            max_step_attempts=1,
            max_validation_cycles_per_step_attempt=1,
            max_transport_attempts_per_model_call=1,
        ),
        model_policy=model_policy or {},
    )


def test_s01_s02_s03_s05_s06_snapshot_is_immutable_and_same_request_keeps_original_task(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls = {"n": 0}
    seen = []

    definition_v1 = AgentDefinition(
        agent_id="snapshot-agent",
        provider="provider-v1",
        model="model-v1",
        prompt_ref="prompt-v1",
        output_schema="schema-v1",
        content_strategy_ref="strategy-v1",
        defaults={
            "max_tokens": 2048,
            "api_key": "MUST_NOT_BE_SNAPSHOTTED",
        },
        metadata={
            "prompt_hash": "prompt-hash-v1",
            "output_schema_version": "1",
            "output_schema_hash": "schema-hash-v1",
            "content_strategy_version": "1",
            "content_strategy_hash": "strategy-hash-v1",
        },
    )

    def handler(payload, context):
        calls["n"] += 1
        seen.append(context["runtime"])
        if calls["n"] == 1:
            raise RuntimeStepError(
                "temporary",
                code="TEMPORARY",
                category=ErrorCategory.EXECUTION,
                retryable=True,
            )
        return payload["value"]

    runtime.register_agent("snapshot-agent", handler, definition_v1)
    request = AgentRequest(
        request_id="req-snapshot",
        agent_id="snapshot-agent",
        input={"value": 7},
        output_schema="schema-v1",
        execution_policy=policy(
            model_policy={
                "max_tokens": 4096,
                "api_key": "SECRET",
                "fallback_models": [],
            }
        ),
    )

    first = runtime.invoke(request)
    assert first.status == RuntimeStatus.PARTIAL
    task = store.get_task(first.task_id)
    assert task is not None
    snapshot_id = task.execution_snapshot_id
    snapshot = store.get_execution_snapshot(snapshot_id)

    assert snapshot.prompt_ref == "prompt-v1"
    assert snapshot.prompt_hash == "prompt-hash-v1"
    assert snapshot.output_schema_ref == "schema-v1"
    assert snapshot.output_schema_version == "1"
    assert snapshot.output_schema_hash == "schema-hash-v1"
    assert snapshot.content_strategy_ref == "strategy-v1"
    assert snapshot.content_strategy_version == "1"
    assert snapshot.model_policy["max_tokens"] == 4096
    assert "api_key" not in snapshot.model_policy
    assert "api_key" not in snapshot.agent_definition["defaults"]

    runtime.register_agent_definition(
        AgentDefinition(
            agent_id="snapshot-agent",
            provider="provider-v2",
            model="model-v2",
            prompt_ref="prompt-v2",
            output_schema="schema-v2",
            content_strategy_ref="strategy-v2",
            metadata={
                "prompt_hash": "prompt-hash-v2",
                "output_schema_version": "2",
                "output_schema_hash": "schema-hash-v2",
            },
        )
    )

    repeated = runtime.invoke(request)
    assert repeated.task_id == first.task_id
    assert calls["n"] == 1
    assert store.get_task(first.task_id).execution_snapshot_id == snapshot_id

    handle = runtime.resume(first.task_id)
    assert handle.status == RuntimeStatus.COMPLETED
    assert calls["n"] == 2
    assert seen[1]["execution_snapshot_id"] == snapshot_id
    assert seen[1]["agent_definition"]["model"] == "model-v1"
    assert seen[1]["agent_definition"]["prompt_ref"] == "prompt-v1"
    assert seen[1]["agent_definition"]["content_strategy_ref"] == "strategy-v1"
    assert seen[1]["model_policy"]["max_tokens"] == 4096

    runs = store.list_runs(first.task_id)
    assert len(runs) == 2
    assert runs[0].execution_snapshot_id == snapshot_id
    assert runs[1].execution_snapshot_id == snapshot_id


def test_s04_workflow_change_does_not_change_resume_definition(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls = {"v1": 0, "new_step": 0}

    def first(_payload, _context):
        calls["v1"] += 1
        if calls["v1"] == 1:
            raise RuntimeStepError(
                "retry later",
                code="RETRY_LATER",
                category=ErrorCategory.EXECUTION,
                retryable=True,
            )
        return "v1-complete"

    def new_step(_payload, _context):
        calls["new_step"] += 1
        return "must-not-run-on-resume"

    runtime.register_agent("a1", first)
    runtime.register_agent("a2", new_step)
    runtime.register_workflow(
        WorkflowDefinition(
            workflow_id="wf-snapshot",
            version="1",
            steps=[StepDefinition(step_id="s1", agent_id="a1")],
        )
    )

    request = WorkflowRequest(
        request_id="req-workflow-snapshot",
        workflow_id="wf-snapshot",
        input={"x": 1},
        execution_policy=policy(),
    )
    first_result = runtime.execute(request)
    assert first_result.status == RuntimeStatus.PARTIAL

    runtime.register_workflow(
        WorkflowDefinition(
            workflow_id="wf-snapshot",
            version="2",
            steps=[
                StepDefinition(step_id="s1", agent_id="a1"),
                StepDefinition(step_id="s2", agent_id="a2", depends_on=["s1"]),
            ],
        )
    )

    handle = runtime.resume(first_result.task_id)
    assert handle.status == RuntimeStatus.COMPLETED
    assert calls == {"v1": 2, "new_step": 0}

    snapshot = store.get_execution_snapshot(
        store.get_task(first_result.task_id).execution_snapshot_id
    )
    assert snapshot.workflow_definition.version == "1"
    assert [step.step_id for step in snapshot.workflow_definition.steps] == ["s1"]


def test_s07_undeclared_fallback_is_not_silently_adopted_on_resume(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls = {"n": 0}
    models_seen = []

    def handler(_payload, context):
        calls["n"] += 1
        models_seen.append(context["runtime"]["agent_definition"]["model"])
        if calls["n"] == 1:
            raise RuntimeStepError(
                "model temporarily unavailable",
                code="MODEL_UNAVAILABLE",
                category=ErrorCategory.EXECUTION,
                retryable=True,
            )
        return "ok"

    runtime.register_agent(
        "model-agent",
        handler,
        AgentDefinition(
            agent_id="model-agent",
            provider="provider",
            model="original-model",
        ),
    )
    result = runtime.invoke(
        AgentRequest(
            request_id="req-no-fallback",
            agent_id="model-agent",
            input={},
            execution_policy=policy(model_policy={"fallback_models": []}),
        )
    )
    assert result.status == RuntimeStatus.PARTIAL

    runtime.register_agent_definition(
        AgentDefinition(
            agent_id="model-agent",
            provider="provider",
            model="new-default-model",
        )
    )
    handle = runtime.resume(result.task_id)

    assert handle.status == RuntimeStatus.COMPLETED
    assert models_seen == ["original-model", "original-model"]


def test_p01_same_step_in_different_partitions_has_different_execution_key(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    keys = {}

    def handler(payload, context):
        keys[payload["partition"]] = context["runtime"]["execution_key"]
        return payload

    runtime.register_agent("partition-agent", handler)

    for partition in ("A", "B"):
        result = runtime.invoke(
            AgentRequest(
                request_id=f"req-partition-{partition}",
                agent_id="partition-agent",
                input={"partition": partition},
                metadata={"partition_key": partition},
            )
        )
        assert result.status == RuntimeStatus.COMPLETED

    assert keys["A"] != keys["B"]


def test_p02_coverage_progress_is_partition_isolated():
    src = SourceRef(
        source_id="same-source-id",
        source_type="ITEMS",
        fingerprint="same-fingerprint",
    )
    universe_a = CoverageUniverse(
        source=src,
        coverage_type="ITEM",
        unit_targets=[CoverageUnit(unit_id="u1")],
        universe_fingerprint="universe-a",
        partition_key="A",
    )
    universe_b = CoverageUniverse(
        source=src,
        coverage_type="ITEM",
        unit_targets=[CoverageUnit(unit_id="u1")],
        universe_fingerprint="universe-b",
        partition_key="B",
    )

    calc = CoverageCalculator()
    coverage_a = calc.calculate(universe_a, processed_unit_ids=["u1"])
    coverage_b = calc.calculate(universe_b, processed_unit_ids=[])

    assert coverage_a.complete is True
    assert coverage_a.partition_key == "A"
    assert coverage_b.complete is False
    assert coverage_b.pending_units == ["u1"]
    assert coverage_b.partition_key == "B"


def _partial(partial_id, partition):
    return CommittedPartialResult(
        partial_id=partial_id,
        chunk_id=f"chunk-{partial_id}",
        execution_key=f"exec-{partial_id}",
        unit_ids=["u1"],
        data={"partition": partition},
        partition_key=partition,
        committed_at=datetime.now(timezone.utc),
    )


def test_p03_isolated_merge_rejects_mixed_partitions():
    with pytest.raises(PartitionMismatchError) as exc:
        ListResultMerger().merge(
            [_partial("p-a", "A"), _partial("p-b", "B")],
            MergeContext(
                merge_key="merge-isolated",
                expected_partial_ids=["p-a", "p-b"],
                partition_policy="ISOLATED",
            ),
        )
    assert exc.value.code == "PARTITION_MISMATCH"


def test_p04_evidence_lineage_does_not_cross_partition_by_default():
    registry = EvidenceRegistry()
    src = SourceRef(
        source_id="source",
        source_type="PDF",
        fingerprint="fp",
    )
    registry.save(
        EvidenceReference(
            evidence_id="e-a",
            source=src,
            locator=EvidenceLocator(type="PAGE", value={"page": 1}),
            partition_key="A",
        )
    )
    registry.save(
        EvidenceReference(
            evidence_id="e-b",
            source=src,
            locator=EvidenceLocator(type="PAGE", value={"page": 2}),
            partition_key="B",
        )
    )

    with pytest.raises(PartitionMismatchError):
        registry.derive(
            evidence_id="e-mixed",
            source=src,
            locator=EvidenceLocator(type="SECTION", value={"name": "mixed"}),
            derived_from=["e-a", "e-b"],
            partition_key="A",
        )


def test_p05_resume_only_reexecutes_pending_work_in_its_partition(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls = {"A": 0, "B": 0}

    def handler(payload, _context):
        partition = payload["partition"]
        calls[partition] += 1
        if partition == "B" and calls[partition] == 1:
            raise RuntimeStepError(
                "retry B",
                code="RETRY_B",
                category=ErrorCategory.EXECUTION,
                retryable=True,
            )
        return partition

    runtime.register_agent("partition-resume", handler)
    result_a = runtime.invoke(
        AgentRequest(
            request_id="req-A",
            agent_id="partition-resume",
            input={"partition": "A"},
            metadata={"partition_key": "A"},
            execution_policy=policy(),
        )
    )
    result_b = runtime.invoke(
        AgentRequest(
            request_id="req-B",
            agent_id="partition-resume",
            input={"partition": "B"},
            metadata={"partition_key": "B"},
            execution_policy=policy(),
        )
    )

    assert result_a.status == RuntimeStatus.COMPLETED
    assert result_b.status == RuntimeStatus.PARTIAL
    runtime.resume(result_b.task_id)

    assert calls == {"A": 1, "B": 2}
    assert len(store.list_runs(result_a.task_id)) == 1
    assert len(store.list_runs(result_b.task_id)) == 2


def test_p06_explicit_cross_partition_merge_preserves_partition_lineage():
    result = ListResultMerger().merge(
        [_partial("p-a", "A"), _partial("p-b", "B")],
        MergeContext(
            merge_key="merge-cross",
            expected_partial_ids=["p-a", "p-b"],
            partition_policy="CROSS_PARTITION",
        ),
    )
    assert result.complete is True
    assert result.metadata["source_partitions"] == ["A", "B"]
    assert result.metadata["partition_policy"] == "CROSS_PARTITION"


def test_x01_cancel_queued_task_directly_enters_cancelled(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    now = datetime.now(timezone.utc)
    request = AgentRequest(
        request_id="req-queued-cancel",
        agent_id="unused",
        input={},
    )
    record = TaskRecord(
        task_id="task-queued",
        request_id=request.request_id,
        request_fingerprint="fp",
        task_type=TaskType.AGENT,
        status=RuntimeStatus.QUEUED,
        input_hash="input-hash",
        created_at=now,
        updated_at=now,
    )
    store.create_or_get_task(record, request=request)

    snapshot = runtime.cancel(record.task_id)

    assert snapshot.status == RuntimeStatus.CANCELLED
    assert snapshot.cancel_requested is True
    assert snapshot.cancel_requested_at is not None


def test_x02_running_cancel_is_cooperative_preserves_commit_and_stops_new_steps(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls = {"s1": 0, "s2": 0}
    cancel_observation = {}

    def first(_payload, context):
        calls["s1"] += 1
        task_id = context["runtime"]["task_id"]
        observed = runtime.cancel(task_id)
        cancel_observation["status"] = observed.status
        cancel_observation["requested"] = observed.cancel_requested
        return {"committed_before_stop": True}

    def second(_payload, _context):
        calls["s2"] += 1
        return "must-not-run"

    runtime.register_agent("cancel-first", first)
    runtime.register_agent("cancel-second", second)

    result = runtime.execute(
        WorkflowRequest(
            request_id="req-running-cancel",
            workflow_id="wf-cancel",
            input={},
            workflow=WorkflowDefinition(
                workflow_id="wf-cancel",
                version="1",
                steps=[
                    StepDefinition(step_id="s1", agent_id="cancel-first"),
                    StepDefinition(
                        step_id="s2",
                        agent_id="cancel-second",
                        depends_on=["s1"],
                    ),
                ],
            ),
            execution_policy=policy(),
        )
    )

    assert cancel_observation == {
        "status": RuntimeStatus.RUNNING,
        "requested": True,
    }
    assert result.status == RuntimeStatus.CANCELLED
    assert result.completion.business_consumable is False
    assert calls == {"s1": 1, "s2": 0}
    assert result.step_results["s1"].status == RuntimeStatus.COMPLETED
    assert result.data["s1"] == {"committed_before_stop": True}
    assert len(store.list_committed_execution_keys(result.task_id)) == 1

    snapshot = runtime.get_task(result.task_id)
    assert snapshot.status == RuntimeStatus.CANCELLED
    assert snapshot.cancel_requested is True
