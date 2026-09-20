from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import runtime.contracts as contracts
from runtime import (
    AgentRequest,
    AgentResult,
    AttemptType,
    CommittedPartialResult,
    ErrorCategory,
    EvidenceLocator,
    EvidenceReference,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    MergeContext,
    PartialResultCandidate,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    RuntimeStepError,
    SourceRef,
    StepDefinition,
    TaskRecord,
    TaskType,
    WorkflowDefinition,
    WorkflowRequest,
    WorkflowResult,
)
from runtime.adapters import (
    LegacyProjector,
    LegacyQualityIssueRuntimeAdapter,
    MajorIssueD01RuntimeAdapter,
    MajorIssueObjectSpec,
)
from runtime.content import (
    EvidenceRegistry,
    InvalidPartialResultError,
    ListResultMerger,
    MergeCoordinator,
    SourceIdentityChangedError,
)
from runtime.engine.runtime import LightweightExecutionEngine
from runtime.reliability import (
    InvalidRuntimeStatusTransition,
    RuntimeStateMachine,
    TaskNotResumableError,
)
from runtime.store import SqliteTaskStore


def retry_policy(
    *,
    mode=ExecutionMode.SINGLE,
    transport=1,
    validation=1,
    step=1,
    provider_calls=4,
):
    return ExecutionPolicy(
        mode=mode,
        transport_retry=RetryPolicy(max_attempts=transport),
        validation_retry=RetryPolicy(max_attempts=validation),
        step_retry=RetryPolicy(max_attempts=step),
        retry_budget=RetryBudget(
            max_provider_calls_per_step=provider_calls,
            max_step_attempts=step,
            max_validation_cycles_per_step_attempt=validation,
            max_transport_attempts_per_model_call=transport,
        ),
        failure_policy=FailurePolicy.PARTIAL,
    )


def test_g01_canonical_contract_is_the_only_runtime_business_surface(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    engine = LightweightExecutionEngine(store)
    engine.register_agent("echo", lambda payload, _ctx: payload)

    agent_result = engine.invoke(
        AgentRequest(
            request_id="g01-agent",
            agent_id="echo",
            input={"value": 1},
        )
    )
    assert isinstance(agent_result, AgentResult)
    assert not hasattr(contracts, "AIRequest")
    assert not hasattr(contracts, "AIResult")

    workflow = WorkflowDefinition(
        workflow_id="g01-wf",
        version="1",
        steps=[StepDefinition(step_id="s1", agent_id="echo")],
    )
    workflow_result = engine.execute(
        WorkflowRequest(
            request_id="g01-workflow",
            workflow_id=workflow.workflow_id,
            workflow=workflow,
            input={"value": 2},
        )
    )
    assert isinstance(workflow_result, WorkflowResult)
    assert workflow_result.step_results["s1"].__class__.__name__ == (
        "StepResultSummary"
    )
    assert workflow_result.step_results["s1"].__class__.__name__ != (
        "StepRunRecord"
    )


def test_g08_seven_state_transition_policy_is_enforced_by_store(tmp_path):
    assert {item.value for item in RuntimeStatus} == {
        "QUEUED",
        "RUNNING",
        "WAITING",
        "PARTIAL",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }
    assert RuntimeStateMachine.is_allowed(
        RuntimeStatus.QUEUED,
        RuntimeStatus.RUNNING,
    )
    assert RuntimeStateMachine.is_allowed(
        RuntimeStatus.RUNNING,
        RuntimeStatus.PARTIAL,
    )
    assert RuntimeStateMachine.is_allowed(
        RuntimeStatus.PARTIAL,
        RuntimeStatus.RUNNING,
    )
    assert not RuntimeStateMachine.is_allowed(
        RuntimeStatus.COMPLETED,
        RuntimeStatus.RUNNING,
    )

    store = SqliteTaskStore(tmp_path / "runtime.db")
    request = AgentRequest(
        request_id="g08-state",
        agent_id="noop",
        input={},
    )
    now = datetime.now(timezone.utc)
    task = TaskRecord(
        task_id="task-g08",
        request_id=request.request_id,
        request_fingerprint="fp",
        task_type=TaskType.AGENT,
        status=RuntimeStatus.QUEUED,
        input_hash="input",
        created_at=now,
        updated_at=now,
    )
    store.create_or_get_task(task, request=request)

    for status in (
        RuntimeStatus.RUNNING,
        RuntimeStatus.PARTIAL,
        RuntimeStatus.RUNNING,
        RuntimeStatus.COMPLETED,
    ):
        task.status = status
        task.updated_at = datetime.now(timezone.utc)
        store.save_task(task)

    task.status = RuntimeStatus.RUNNING
    with pytest.raises(InvalidRuntimeStatusTransition):
        store.save_task(task)


def _parallel_case(tmp_path, name, failing_required, retryable, optional=False):
    store = SqliteTaskStore(tmp_path / f"{name}.db")
    engine = LightweightExecutionEngine(store)

    def ok(payload, _ctx):
        return {"ok": payload.get("value", 1)}

    def fail(_payload, _ctx):
        raise RuntimeStepError(
            name,
            code=name.upper(),
            category=ErrorCategory.EXECUTION,
            retryable=retryable,
        )

    engine.register_agent("ok", ok)
    engine.register_agent("fail", fail)
    workflow = WorkflowDefinition(
        workflow_id=f"wf-{name}",
        version="1",
        steps=[
            StepDefinition(
                step_id="good",
                agent_id="ok",
                required_for_completion=True,
            ),
            StepDefinition(
                step_id="bad",
                agent_id="fail",
                required_for_completion=not optional,
            ),
        ],
    )
    result = engine.execute(
        WorkflowRequest(
            request_id=f"req-{name}",
            workflow_id=workflow.workflow_id,
            workflow=workflow,
            input={"value": 7},
            execution_policy=retry_policy(
                mode=ExecutionMode.PARALLEL,
                provider_calls=4,
            ),
        )
    )
    return store, result


def test_g09_parallel_required_resumable_failure_is_partial(tmp_path):
    _, result = _parallel_case(
        tmp_path,
        "g09-resumable",
        failing_required=True,
        retryable=True,
    )
    assert result.status == RuntimeStatus.PARTIAL
    assert result.step_results["good"].status == RuntimeStatus.COMPLETED
    assert result.step_results["bad"].status == RuntimeStatus.PARTIAL


def test_g09_parallel_required_terminal_failure_is_failed(tmp_path):
    _, result = _parallel_case(
        tmp_path,
        "g09-terminal",
        failing_required=True,
        retryable=False,
    )
    assert result.status == RuntimeStatus.FAILED
    assert result.step_results["good"].status == RuntimeStatus.COMPLETED
    assert result.step_results["bad"].status == RuntimeStatus.FAILED


def test_g09_parallel_optional_failure_can_complete_with_warning(tmp_path):
    _, result = _parallel_case(
        tmp_path,
        "g09-optional",
        failing_required=False,
        retryable=False,
        optional=True,
    )
    assert result.status == RuntimeStatus.COMPLETED
    assert result.completion.business_consumable is True
    assert [item.code for item in result.warnings] == [
        "OPTIONAL_STEP_INCOMPLETE"
    ]


def test_r01_transport_retry_provider_calls_match_trace(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    engine = LightweightExecutionEngine(store)
    calls = {"n": 0}

    def handler(_payload, _ctx):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeStepError(
                "transport",
                code="TEMP_TRANSPORT",
                category=ErrorCategory.TRANSPORT,
                retryable=True,
            )
        return {"ok": True}

    engine.register_agent("transport", handler)
    result = engine.invoke(
        AgentRequest(
            request_id="r01",
            agent_id="transport",
            input={},
            execution_policy=retry_policy(
                transport=3,
                provider_calls=4,
            ),
        )
    )
    assert result.status == RuntimeStatus.COMPLETED
    assert calls["n"] == 3
    assert result.execution.provider_calls == 3


def test_r02_validation_correction_consumes_same_provider_budget(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    engine = LightweightExecutionEngine(store)
    calls = {"n": 0}

    def handler(_payload, _ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeStepError(
                "schema correction required",
                code="SCHEMA_INVALID",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            )
        return {"valid": True}

    engine.register_agent("validation", handler)
    result = engine.invoke(
        AgentRequest(
            request_id="r02",
            agent_id="validation",
            input={},
            execution_policy=retry_policy(
                validation=2,
                provider_calls=3,
            ),
        )
    )
    assert result.status == RuntimeStatus.COMPLETED
    assert calls["n"] == 2
    assert result.execution.provider_calls == 2

    run = store.list_runs(result.task_id)[0]
    step_run = store.list_step_runs(run.run_id)[0]
    attempts = store.list_attempts(step_run.step_run_id)
    assert [item.provider_call_seq for item in attempts] == [1, 2]
    assert [item.validation_cycle_no for item in attempts] == [1, 2]


def test_r03_step_retry_increments_outer_attempt_without_budget_reset(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    engine = LightweightExecutionEngine(store)
    calls = {"n": 0}

    def handler(_payload, _ctx):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeStepError(
                "retry step",
                code="TEMP_EXECUTION",
                category=ErrorCategory.EXECUTION,
                retryable=True,
            )
        return {"ok": True}

    engine.register_agent("step-retry", handler)
    result = engine.invoke(
        AgentRequest(
            request_id="r03",
            agent_id="step-retry",
            input={},
            execution_policy=retry_policy(
                step=3,
                provider_calls=3,
            ),
        )
    )
    assert result.status == RuntimeStatus.COMPLETED
    assert calls["n"] == 3
    assert result.execution.provider_calls == 3

    run = store.list_runs(result.task_id)[0]
    step_run = store.list_step_runs(run.run_id)[0]
    attempts = store.list_attempts(step_run.step_run_id)
    assert [item.step_attempt_no for item in attempts] == [1, 2, 3]
    assert len({item.execution_key for item in attempts}) == 1


def test_r05_sdk_implicit_retry_is_disabled_at_provider_adapter_boundary(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    engine = LightweightExecutionEngine(store)
    observed = {}

    def handler(_payload, context):
        observed.update(context["runtime"]["sdk_retry_policy"])
        return {"ok": True}

    engine.register_agent("sdk-guard", handler)
    result = engine.invoke(
        AgentRequest(
            request_id="r05",
            agent_id="sdk-guard",
            input={},
        )
    )
    assert result.status == RuntimeStatus.COMPLETED
    assert observed == {
        "implicit_retry_enabled": False,
        "adapter_must_report_actual_provider_requests": True,
    }
    assert result.execution.provider_calls == 1


def test_r06_g11_budget_exhausted_with_committed_progress_is_partial(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    engine = LightweightExecutionEngine(store)
    calls = {"good": 0, "bad": 0}

    def good(_payload, _ctx):
        calls["good"] += 1
        return {"committed": True}

    def bad(_payload, _ctx):
        calls["bad"] += 1
        raise RuntimeStepError(
            "still failing",
            code="TEMP",
            category=ErrorCategory.EXECUTION,
            retryable=True,
        )

    engine.register_agent("good", good)
    engine.register_agent("bad", bad)
    workflow = WorkflowDefinition(
        workflow_id="r06-wf",
        version="1",
        steps=[
            StepDefinition(step_id="good", agent_id="good"),
            StepDefinition(
                step_id="bad",
                agent_id="bad",
                depends_on=["good"],
            ),
        ],
    )
    result = engine.execute(
        WorkflowRequest(
            request_id="r06",
            workflow_id=workflow.workflow_id,
            workflow=workflow,
            input={},
            execution_policy=retry_policy(
                mode=ExecutionMode.SEQUENTIAL,
                step=3,
                provider_calls=2,
            ),
        )
    )

    assert result.status == RuntimeStatus.PARTIAL
    assert result.data == {"good": {"committed": True}}
    assert result.completion.business_consumable is False
    assert calls == {"good": 1, "bad": 2}
    assert store.has_committed_progress(result.task_id) is True


def test_r07_budget_exhausted_without_recovery_path_is_failed(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    engine = LightweightExecutionEngine(store)
    calls = {"n": 0}

    def bad(_payload, _ctx):
        calls["n"] += 1
        raise RuntimeStepError(
            "still failing",
            code="TEMP",
            category=ErrorCategory.EXECUTION,
            retryable=True,
        )

    engine.register_agent("bad", bad)
    result = engine.invoke(
        AgentRequest(
            request_id="r07",
            agent_id="bad",
            input={},
            execution_policy=retry_policy(
                step=3,
                provider_calls=2,
            ),
        )
    )
    assert result.status == RuntimeStatus.FAILED
    assert result.completion.business_consumable is False
    assert calls["n"] == 2
    assert store.has_committed_progress(result.task_id) is False


def test_c04_exact_three_committed_partials_merge_retry_has_one_commit(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    partials = [
        CommittedPartialResult(
            partial_id=f"p{i}",
            chunk_id=f"c{i}",
            execution_key=f"e{i}",
            unit_ids=[f"u{i}"],
            data={"id": i},
            committed_at=datetime.now(timezone.utc),
        )
        for i in range(1, 4)
    ]
    context = MergeContext(
        merge_key="c04-three-partials",
        expected_partial_ids=["p1", "p2", "p3"],
    )

    class FailOnce:
        def __init__(self):
            self.calls = 0
            self.delegate = ListResultMerger()

        def merge(self, inputs, merge_context):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("merge fault")
            return self.delegate.merge(inputs, merge_context)

    merger = FailOnce()
    coordinator = MergeCoordinator(store)
    with pytest.raises(RuntimeError):
        coordinator.merge_and_commit(merger, partials, context)

    second = coordinator.merge_and_commit(merger, partials, context)
    third = coordinator.merge_and_commit(merger, partials, context)
    assert second.complete is True
    assert second.derived_from_partial_ids == ["p1", "p2", "p3"]
    assert third == second
    assert merger.calls == 2
    assert store.get_merge_result(context.merge_key) == second


def test_c05_uncommitted_candidate_never_enters_merger():
    candidate = PartialResultCandidate(
        chunk_id="c1",
        execution_key="e1",
        unit_ids=["u1"],
        data={"bad": True},
        schema_valid=False,
        complete_object=True,
    )
    with pytest.raises(InvalidPartialResultError):
        ListResultMerger().merge(
            [candidate],
            MergeContext(
                merge_key="c05",
                expected_partial_ids=["not-committed"],
            ),
        )


def _d01_source(fingerprint="fp-v1", revision="1"):
    return SourceRef(
        source_id="d01-source",
        source_type="MAJOR_REVIEW",
        revision=revision,
        content_hash=f"hash-{fingerprint}",
        fingerprint=fingerprint,
        uri="file://same-name.docx",
    )


def test_e04_d01_resume_rejects_changed_source_identity(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    engine = LightweightExecutionEngine(store)
    original = _d01_source()

    def provider(_input, pending, _ctx):
        result = []
        for item in pending:
            complete = item["object_id"] == "o1"
            result.append(
                {
                    "object_id": item["object_id"],
                    "data": {"id": item["object_id"]},
                    "schema_valid": True,
                    "complete_object": complete,
                    "finish_reason": "stop" if complete else "length",
                }
            )
        return result

    adapter = MajorIssueD01RuntimeAdapter(engine, store, provider)
    first = adapter.execute_partition(
        case_id="CASE-E04",
        issue_version_id="V1",
        partition_key="P1",
        source=original,
        expected_objects=[
            MajorIssueObjectSpec(object_id="o1", unit_id="u1"),
            MajorIssueObjectSpec(object_id="o2", unit_id="u2"),
        ],
    )
    assert first.status == RuntimeStatus.PARTIAL

    changed = _d01_source(fingerprint="fp-v2", revision="2")
    with pytest.raises(SourceIdentityChangedError) as exc:
        adapter.resume(first.task_id, actual_source=changed)
    assert exc.value.code == "SOURCE_IDENTITY_CHANGED"
    assert store.list_runs(first.task_id).__len__() == 1


def test_e07_merge_evidence_derived_from_traces_to_original_sources():
    source = SourceRef(
        source_id="e07",
        source_type="PDF",
        revision="1",
        fingerprint="fp",
    )
    registry = EvidenceRegistry()
    for eid, page in (("e1", 1), ("e2", 2)):
        registry.save(
            EvidenceReference(
                evidence_id=eid,
                source=source,
                locator=EvidenceLocator(
                    type="PAGE",
                    value={"page": page},
                ),
            )
        )
    registry.derive(
        evidence_id="e-merged",
        source=source,
        locator=EvidenceLocator(
            type="SECTION",
            value={"name": "merge"},
        ),
        derived_from=["e1", "e2"],
    )
    partial = CommittedPartialResult(
        partial_id="p1",
        chunk_id="c1",
        execution_key="x1",
        unit_ids=["u1"],
        data={"merged": True},
        evidence_ids=["e-merged"],
        committed_at=datetime.now(timezone.utc),
    )
    result = ListResultMerger(registry).merge(
        [partial],
        MergeContext(
            merge_key="e07",
            expected_partial_ids=["p1"],
        ),
    )
    assert result.evidence[0].evidence_id == "e-merged"
    assert result.evidence[0].derived_from == ["e1", "e2"]
    assert {
        item.evidence_id
        for item in registry.lineage("e-merged")
    } == {"e1", "e2", "e-merged"}


def test_p02_partition_a_checkpoint_does_not_advance_failed_partition_b(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    engine = LightweightExecutionEngine(store)

    engine.register_agent("ok", lambda _p, _c: {"ok": True})

    def retryable(_p, _c):
        raise RuntimeStepError(
            "pending",
            code="PENDING",
            category=ErrorCategory.EXECUTION,
            retryable=True,
        )

    engine.register_agent("pending", retryable)
    a = engine.invoke(
        AgentRequest(
            request_id="p02-a",
            agent_id="ok",
            input={},
            metadata={"partition_key": "A"},
            execution_policy=retry_policy(provider_calls=4),
        )
    )
    b = engine.invoke(
        AgentRequest(
            request_id="p02-b",
            agent_id="pending",
            input={},
            metadata={"partition_key": "B"},
            execution_policy=retry_policy(provider_calls=4),
        )
    )

    assert a.status == RuntimeStatus.COMPLETED
    assert b.status == RuntimeStatus.PARTIAL
    assert len(store.list_checkpoints(a.task_id)) == 1
    assert len(store.list_checkpoints(b.task_id)) == 0
    assert len(store.list_committed_execution_keys(a.task_id)) == 1
    assert len(store.list_committed_execution_keys(b.task_id)) == 0


def test_p04_same_locator_does_not_reuse_evidence_across_partitions():
    registry = EvidenceRegistry()
    source = SourceRef(
        source_id="p04",
        source_type="PDF",
        fingerprint="fp",
    )
    locator = EvidenceLocator(
        type="PAGE",
        value={"page": 1, "table": "same"},
    )
    registry.save(
        EvidenceReference(
            evidence_id="e-a",
            source=source,
            locator=locator,
            partition_key="A",
        )
    )
    registry.save(
        EvidenceReference(
            evidence_id="e-b",
            source=source,
            locator=locator,
            partition_key="B",
        )
    )
    assert registry.get("e-a").partition_key == "A"
    assert registry.get("e-b").partition_key == "B"
    assert registry.get("e-a").evidence_id != registry.get("e-b").evidence_id


def _write_quality_config(root: Path) -> None:
    (root / "config").mkdir(parents=True)
    prompts = root / "quality_knowledge" / "prompts"
    prompts.mkdir(parents=True)
    (root / "config" / "model.yaml").write_text(
        """
ai:
  provider: openai_compatible
  model: test-model
  max_retries: 0
  timeout_seconds: 30
quality_issue_agents:
  quality:
    provider: openai_compatible
    model: quality-model
""".strip(),
        encoding="utf-8",
    )
    for stage in (
        "occurrence",
        "escape",
        "recurrence",
        "capability_gap",
    ):
        (prompts / f"{stage}.md").write_text(
            f"{stage} prompt",
            encoding="utf-8",
        )


def _quality_adapter(tmp_path, handlers, projector=None, legacy_reader=None):
    root = tmp_path / "app"
    _write_quality_config(root)
    store = SqliteTaskStore(tmp_path / "quality.db")
    runtime = LightweightExecutionEngine(store)
    adapter = LegacyQualityIssueRuntimeAdapter(
        runtime,
        store,
        root,
        agent_id="quality",
        stage_handlers=handlers,
        projector=projector,
        legacy_reader=legacy_reader,
    )
    return store, runtime, adapter


def _quality_handlers(evidence_payload=None):
    handlers = {}
    for stage in (
        "occurrence",
        "escape",
        "recurrence",
        "capability_gap",
    ):
        def make(name):
            def handler(_payload, _context):
                result = {"stage": name}
                if name == "occurrence" and evidence_payload is not None:
                    result["existing_evidence"] = evidence_payload
                return result
            return handler
        handlers[stage] = make(stage)
    return handlers


def _issue():
    return {
        "knowledge_id": "QK-D11",
        "issue_version_id": "V1",
        "issue_fact": {"title": "demo"},
    }


def test_lq04_projection_replay_is_idempotent(tmp_path):
    store, _, adapter = _quality_adapter(
        tmp_path,
        _quality_handlers(),
    )
    result = adapter.execute_issue(
        "QK-D11",
        issue_input=_issue(),
        issue_version_id="V1",
        request_id="lq04",
    )
    binding = store.get_legacy_binding_by_run(result.run_id)
    assert binding is not None
    applied = store.list_projection_events(
        run_id=result.run_id,
        status="APPLIED",
    )
    assert len(applied) == 4

    for event in applied:
        adapter.projector.project_event(event)
        adapter.projector.project_event(event)

    stages = store.list_legacy_stage_projections(
        binding.legacy_analysis_set_id
    )
    assert len(stages) == 4
    assert len({item["legacy_stage"] for item in stages}) == 4
    assert store.get_legacy_analysis_set(
        binding.legacy_analysis_set_id
    ) is not None


def test_lq05_runtime_state_truth_ignores_projection_delay_and_manual_legacy_status(tmp_path):
    fail_all = {"enabled": True}

    def fail_hook(_event):
        if fail_all["enabled"]:
            raise RuntimeError("projection delayed")

    root = tmp_path / "app"
    _write_quality_config(root)
    store = SqliteTaskStore(tmp_path / "quality.db")
    runtime = LightweightExecutionEngine(store)
    projector = LegacyProjector(store, fail_hook=fail_hook)
    adapter = LegacyQualityIssueRuntimeAdapter(
        runtime,
        store,
        root,
        agent_id="quality",
        stage_handlers=_quality_handlers(),
        projector=projector,
    )
    result = adapter.execute_issue(
        "QK-D11",
        issue_input=_issue(),
        issue_version_id="V1",
        request_id="lq05",
    )
    assert result.status == RuntimeStatus.COMPLETED
    assert store.list_projection_events(
        run_id=result.run_id,
        status="PENDING",
    )

    progress = adapter.get_progress(result.task_id)
    assert progress["runtime_status"] == "COMPLETED"
    with pytest.raises(TaskNotResumableError):
        runtime.resume(result.task_id)

    binding = store.get_legacy_binding_by_run(result.run_id)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            UPDATE legacy_quality_analysis_set_projection
            SET status='PARTIAL_FAILED'
            WHERE legacy_analysis_set_id=?
            """,
            (binding.legacy_analysis_set_id,),
        )
        connection.commit()

    runtime_snapshot = runtime.get_task(result.task_id)
    assert runtime_snapshot.status == RuntimeStatus.COMPLETED


def test_lq07_existing_business_evidence_survives_projection_unchanged(tmp_path):
    business_evidence = {
        "field_id": "root_cause",
        "source": {
            "source_id": "ITR-1",
            "fingerprint": "business-fp",
        },
        "locator": {
            "type": "BUSINESS_RECORD",
            "record_id": "ITR-1",
            "field": "root_cause",
        },
    }
    store, _, adapter = _quality_adapter(
        tmp_path,
        _quality_handlers(business_evidence),
    )
    result = adapter.execute_issue(
        "QK-D11",
        issue_input=_issue(),
        issue_version_id="V1",
        request_id="lq07",
    )
    projected = adapter.get_projected_analysis_set(result.run_id)
    occurrence = next(
        item
        for item in projected["stages"]
        if item["legacy_stage"] == "occurrence"
    )
    assert occurrence["result"]["existing_evidence"] == business_evidence
