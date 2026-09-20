from __future__ import annotations

from runtime import (
    ErrorCategory,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    RuntimeStepError,
    SqliteTaskStore,
    StepDefinition,
    WorkflowDefinition,
    WorkflowRequest,
)
from runtime.engine.compare import EngineCompareHarness
from runtime.engine.langgraph_engine import LangGraphExecutionEngine
from runtime.engine.runtime import LightweightExecutionEngine


def policy(mode=ExecutionMode.SEQUENTIAL):
    return ExecutionPolicy(
        mode=mode,
        transport_retry=RetryPolicy(max_attempts=1),
        validation_retry=RetryPolicy(max_attempts=1),
        step_retry=RetryPolicy(max_attempts=1),
        retry_budget=RetryBudget(
            max_provider_calls_per_step=4,
            max_step_attempts=1,
            max_validation_cycles_per_step_attempt=1,
            max_transport_attempts_per_model_call=1,
        ),
        failure_policy=FailurePolicy.PARTIAL,
    )


def engine_pair(tmp_path):
    left_store = SqliteTaskStore(tmp_path / "light.db")
    right_store = SqliteTaskStore(tmp_path / "graph.db")
    return (
        LightweightExecutionEngine(left_store),
        left_store,
        LangGraphExecutionEngine(right_store),
        right_store,
    )


def compare_results(left_result, left_store, right_result, right_store):
    left = EngineCompareHarness.observe(
        engine_name="lightweight",
        result=left_result,
        store=left_store,
    )
    right = EngineCompareHarness.observe(
        engine_name="langgraph",
        result=right_result,
        store=right_store,
    )
    comparison = EngineCompareHarness.compare(left, right)
    assert comparison.equivalent is True, comparison.model_dump()
    assert comparison.mismatches == []
    return comparison


def test_d10_completed_sequential_contract_and_completion_are_equivalent(tmp_path):
    left, left_store, right, right_store = engine_pair(tmp_path)

    def first(payload, _context):
        return payload["x"] + 1

    def second(_payload, context):
        return context["dependencies"]["s1"] * 10

    for engine in (left, right):
        engine.register_agent("a1", first)
        engine.register_agent("a2", second)

    workflow = WorkflowDefinition(
        workflow_id="wf-d10-sequential",
        version="1",
        steps=[
            StepDefinition(step_id="s1", agent_id="a1"),
            StepDefinition(
                step_id="s2",
                agent_id="a2",
                depends_on=["s1"],
            ),
        ],
    )
    request = WorkflowRequest(
        request_id="d10-sequential",
        workflow_id=workflow.workflow_id,
        input={"x": 2},
        workflow=workflow,
        execution_policy=policy(),
    )

    left_result = left.execute(request)
    right_result = right.execute(request)

    comparison = compare_results(
        left_result,
        left_store,
        right_result,
        right_store,
    )
    assert comparison.left.status == RuntimeStatus.COMPLETED
    assert comparison.left.data == {"s1": 3, "s2": 30}
    assert comparison.left.completion["business_consumable"] is True
    assert comparison.left.checkpoint_count == 2


def test_d10_parallel_ready_wave_is_equivalent(tmp_path):
    left, left_store, right, right_store = engine_pair(tmp_path)

    def one(payload, _context):
        return {"one": payload["v"]}

    def two(payload, _context):
        return {"two": payload["v"] * 2}

    for engine in (left, right):
        engine.register_agent("one", one)
        engine.register_agent("two", two)

    workflow = WorkflowDefinition(
        workflow_id="wf-d10-parallel",
        version="1",
        steps=[
            StepDefinition(step_id="s1", agent_id="one"),
            StepDefinition(step_id="s2", agent_id="two"),
        ],
    )
    request = WorkflowRequest(
        request_id="d10-parallel",
        workflow_id=workflow.workflow_id,
        input={"v": 5},
        workflow=workflow,
        execution_policy=policy(ExecutionMode.PARALLEL),
    )

    left_result = left.execute(request)
    right_result = right.execute(request)

    comparison = compare_results(
        left_result,
        left_store,
        right_result,
        right_store,
    )
    assert comparison.left.status == RuntimeStatus.COMPLETED
    assert comparison.left.step_statuses == {
        "s1": RuntimeStatus.COMPLETED,
        "s2": RuntimeStatus.COMPLETED,
    }
    assert comparison.left.committed_execution_count == 2


def test_d10_partial_resume_checkpoint_and_committed_reuse_are_equivalent(tmp_path):
    left, left_store, right, right_store = engine_pair(tmp_path)
    calls = {
        "light": {"s1": 0, "s2": 0},
        "graph": {"s1": 0, "s2": 0},
    }

    def handlers(bucket):
        def first(payload, _context):
            calls[bucket]["s1"] += 1
            return payload["x"] + 1

        def second(_payload, context):
            calls[bucket]["s2"] += 1
            if calls[bucket]["s2"] == 1:
                raise RuntimeStepError(
                    "temporary",
                    code="TEMPORARY",
                    category=ErrorCategory.EXECUTION,
                    retryable=True,
                )
            return context["dependencies"]["s1"] * 10

        return first, second

    l1, l2 = handlers("light")
    r1, r2 = handlers("graph")
    left.register_agent("a1", l1)
    left.register_agent("a2", l2)
    right.register_agent("a1", r1)
    right.register_agent("a2", r2)

    workflow = WorkflowDefinition(
        workflow_id="wf-d10-resume",
        version="1",
        steps=[
            StepDefinition(step_id="s1", agent_id="a1"),
            StepDefinition(
                step_id="s2",
                agent_id="a2",
                depends_on=["s1"],
            ),
        ],
    )
    request = WorkflowRequest(
        request_id="d10-resume",
        workflow_id=workflow.workflow_id,
        input={"x": 3},
        workflow=workflow,
        execution_policy=policy(),
    )

    left_first = left.execute(request)
    right_first = right.execute(request)
    first_compare = compare_results(
        left_first,
        left_store,
        right_first,
        right_store,
    )
    assert first_compare.left.status == RuntimeStatus.PARTIAL
    assert first_compare.left.checkpoint_count == 1
    assert first_compare.left.provider_calls == 2
    assert first_compare.left.completion["business_consumable"] is False

    left_handle = left.resume(left_first.task_id)
    right_handle = right.resume(right_first.task_id)
    assert left_handle.status == RuntimeStatus.COMPLETED
    assert right_handle.status == RuntimeStatus.COMPLETED

    left_final = left.get_task(left_first.task_id).result
    right_final = right.get_task(right_first.task_id).result
    final_compare = compare_results(
        left_final,
        left_store,
        right_final,
        right_store,
    )

    assert final_compare.left.status == RuntimeStatus.COMPLETED
    assert final_compare.left.resumed is True
    assert final_compare.left.run_count == 2
    assert final_compare.left.latest_run_sequence == 2
    assert final_compare.left.latest_resume_of_run_id_present is True
    assert final_compare.left.checkpoint_count == 2
    assert final_compare.left.provider_calls == 3
    assert calls["light"] == {"s1": 1, "s2": 2}
    assert calls["graph"] == {"s1": 1, "s2": 2}


def test_d10_required_terminal_failure_is_equivalent(tmp_path):
    left, left_store, right, right_store = engine_pair(tmp_path)

    def fail(_payload, _context):
        raise RuntimeStepError(
            "terminal",
            code="TERMINAL",
            category=ErrorCategory.BUSINESS,
            retryable=False,
        )

    for engine in (left, right):
        engine.register_agent("fail", fail)

    workflow = WorkflowDefinition(
        workflow_id="wf-d10-failed",
        version="1",
        steps=[
            StepDefinition(step_id="required", agent_id="fail"),
        ],
    )
    request = WorkflowRequest(
        request_id="d10-failed",
        workflow_id=workflow.workflow_id,
        input={},
        workflow=workflow,
        execution_policy=policy(),
    )

    comparison = compare_results(
        left.execute(request),
        left_store,
        right.execute(request),
        right_store,
    )
    assert comparison.left.status == RuntimeStatus.FAILED
    assert comparison.left.error_code == "TERMINAL"
    assert comparison.left.error_retryable is False
    assert comparison.left.completion["business_consumable"] is False


def test_d10_optional_failure_can_still_complete_with_same_warning(tmp_path):
    left, left_store, right, right_store = engine_pair(tmp_path)

    def optional_fail(_payload, _context):
        raise RuntimeStepError(
            "optional terminal",
            code="OPTIONAL_TERMINAL",
            category=ErrorCategory.BUSINESS,
            retryable=False,
        )

    def required_ok(_payload, _context):
        return {"ok": True}

    for engine in (left, right):
        engine.register_agent("optional", optional_fail)
        engine.register_agent("required", required_ok)

    workflow = WorkflowDefinition(
        workflow_id="wf-d10-optional",
        version="1",
        steps=[
            StepDefinition(
                step_id="optional",
                agent_id="optional",
                required_for_completion=False,
            ),
            StepDefinition(
                step_id="required",
                agent_id="required",
                depends_on=["optional"],
                required_for_completion=True,
            ),
        ],
    )
    request = WorkflowRequest(
        request_id="d10-optional",
        workflow_id=workflow.workflow_id,
        input={},
        workflow=workflow,
        execution_policy=policy(),
    )

    comparison = compare_results(
        left.execute(request),
        left_store,
        right.execute(request),
        right_store,
    )
    assert comparison.left.status == RuntimeStatus.COMPLETED
    assert comparison.left.warning_codes == ["OPTIONAL_STEP_INCOMPLETE"]
    assert comparison.left.step_statuses == {
        "optional": RuntimeStatus.FAILED,
        "required": RuntimeStatus.COMPLETED,
    }
    assert comparison.left.completion["business_consumable"] is True
