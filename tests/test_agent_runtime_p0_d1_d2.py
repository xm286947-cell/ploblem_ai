from __future__ import annotations

from runtime import (
    AgentRequest,
    ExecutionMode,
    ExecutionPolicy,
    LightweightExecutionEngine,
    RuntimeStatus,
    SqliteTaskStore,
    StepDefinition,
    WorkflowDefinition,
    WorkflowRequest,
)


def make_runtime(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    return LightweightExecutionEngine(store), store


def test_d1_task_store_survives_process_reopen(tmp_path):
    runtime, _store = make_runtime(tmp_path)
    runtime.register_agent("echo", lambda payload, context: {"echo": payload, "ctx": context})

    result = runtime.invoke(
        AgentRequest(
            request_id="req-d1-persist",
            agent_id="echo",
            input={"value": 7},
            context={"source": "test"},
        )
    )
    assert result.status == RuntimeStatus.COMPLETED

    reopened = SqliteTaskStore(tmp_path / "runtime.db")
    snapshot = reopened.get_task_snapshot(result.task_id)
    assert snapshot.status == RuntimeStatus.COMPLETED
    assert snapshot.result is not None
    assert snapshot.result.data["echo"] == {"value": 7}
    assert len(reopened.list_runs(result.task_id)) == 1
    assert len(reopened.list_step_runs(snapshot.current_run_id)) == 1


def test_d2_single_invoke_and_submit_get_task(tmp_path):
    runtime, _store = make_runtime(tmp_path)
    runtime.register_agent("double", lambda payload, _context: payload["n"] * 2)

    result = runtime.invoke(
        AgentRequest(
            request_id="req-single",
            agent_id="double",
            input={"n": 4},
        )
    )
    assert result.status == RuntimeStatus.COMPLETED
    assert result.data == 8

    handle = runtime.submit(
        AgentRequest(
            request_id="req-submit",
            agent_id="double",
            input={"n": 5},
        )
    )
    snapshot = runtime.get_task(handle.task_id)
    assert handle.status == RuntimeStatus.COMPLETED
    assert snapshot.status == RuntimeStatus.COMPLETED
    assert snapshot.result.data == 10


def test_d2_sequential_workflow_respects_dependencies(tmp_path):
    runtime, _store = make_runtime(tmp_path)
    call_order = []

    def first(payload, _context):
        call_order.append("first")
        return payload["x"] + 1

    def second(payload, context):
        call_order.append("second")
        return context["dependencies"]["s1"] * 10

    runtime.register_agent("a1", first)
    runtime.register_agent("a2", second)

    workflow = WorkflowDefinition(
        workflow_id="wf-seq",
        version="1",
        steps=[
            StepDefinition(step_id="s1", agent_id="a1"),
            StepDefinition(step_id="s2", agent_id="a2", depends_on=["s1"]),
        ],
    )

    result = runtime.execute(
        WorkflowRequest(
            request_id="req-seq",
            workflow_id="wf-seq",
            input={"x": 2},
            workflow=workflow,
            execution_policy=ExecutionPolicy(mode=ExecutionMode.SEQUENTIAL),
        )
    )
    assert result.status == RuntimeStatus.COMPLETED
    assert call_order == ["first", "second"]
    assert result.step_results["s1"].data == 3
    assert result.step_results["s2"].data == 30


def test_d2_parallel_workflow_runs_same_level_steps(tmp_path):
    runtime, _store = make_runtime(tmp_path)
    runtime.register_agent("left", lambda payload, _context: payload["x"] + 1)
    runtime.register_agent("right", lambda payload, _context: payload["x"] + 2)

    workflow = WorkflowDefinition(
        workflow_id="wf-parallel",
        version="1",
        steps=[
            StepDefinition(step_id="left", agent_id="left"),
            StepDefinition(step_id="right", agent_id="right"),
        ],
    )

    result = runtime.execute(
        WorkflowRequest(
            request_id="req-parallel",
            workflow_id="wf-parallel",
            input={"x": 10},
            workflow=workflow,
            execution_policy=ExecutionPolicy(mode=ExecutionMode.PARALLEL),
        )
    )
    assert result.status == RuntimeStatus.COMPLETED
    assert result.step_results["left"].data == 11
    assert result.step_results["right"].data == 12
    assert set(result.data) == {"left", "right"}
