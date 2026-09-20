from __future__ import annotations

import sqlite3

import pytest

from runtime import (
    AgentRequest,
    ErrorCategory,
    ExecutionMode,
    ExecutionPolicy,
    IdempotencyConflictError,
    LightweightExecutionEngine,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    RuntimeStepError,
    SimulatedCrash,
    SqliteTaskStore,
    StepDefinition,
    WorkflowDefinition,
    WorkflowRequest,
)


def make_policy(*, provider_calls=2, step_attempts=1, transport=1, validation=1):
    return ExecutionPolicy(
        mode=ExecutionMode.SEQUENTIAL,
        transport_retry=RetryPolicy(max_attempts=transport),
        validation_retry=RetryPolicy(max_attempts=validation),
        step_retry=RetryPolicy(max_attempts=step_attempts),
        retry_budget=RetryBudget(
            max_provider_calls_per_step=provider_calls,
            max_step_attempts=step_attempts,
            max_validation_cycles_per_step_attempt=validation,
            max_transport_attempts_per_model_call=transport,
        ),
    )


def test_g02_request_idempotency_reuses_same_task_without_extra_call(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls = {"n": 0}

    def handler(payload, _context):
        calls["n"] += 1
        return {"value": payload["value"]}

    runtime.register_agent("echo", handler)
    request = AgentRequest(
        request_id="req-idempotent",
        agent_id="echo",
        input={"value": 1},
    )

    first = runtime.invoke(request)
    second = runtime.invoke(request)

    assert first.task_id == second.task_id
    assert first.run_id == second.run_id
    assert calls["n"] == 1

    with pytest.raises(IdempotencyConflictError) as exc:
        runtime.invoke(
            AgentRequest(
                request_id="req-idempotent",
                agent_id="echo",
                input={"value": 2},
            )
        )
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    assert calls["n"] == 1


def test_g05_crash_after_provider_return_replays_but_commits_once(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    crash_once = {"armed": True}

    def fault(point):
        if point == "after_provider_return_before_commit" and crash_once["armed"]:
            crash_once["armed"] = False
            raise SimulatedCrash(point)

    runtime = LightweightExecutionEngine(store, fault_injector=fault)
    calls = {"n": 0}

    def handler(payload, _context):
        calls["n"] += 1
        return {"value": payload["value"]}

    runtime.register_agent("echo", handler)
    request = AgentRequest(
        request_id="req-crash-return",
        agent_id="echo",
        input={"value": 7},
        execution_policy=make_policy(provider_calls=2),
    )

    with pytest.raises(SimulatedCrash):
        runtime.invoke(request)

    task = store.get_task_by_request_id("req-crash-return")
    assert task is not None
    assert task.status == RuntimeStatus.RUNNING
    assert len(store.list_committed_execution_keys(task.task_id)) == 0

    resumed = LightweightExecutionEngine(store)
    resumed.register_agent("echo", handler)
    handle = resumed.resume(task.task_id)

    assert handle.status == RuntimeStatus.COMPLETED
    assert calls["n"] == 2
    assert len(store.list_runs(task.task_id)) == 2
    assert len(store.list_committed_execution_keys(task.task_id)) == 1
    assert len(store.list_checkpoints(task.task_id)) == 1


def test_g07_commit_success_before_task_status_update_does_not_recall_provider(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    crash_once = {"armed": True}

    def fault(point):
        if point == "after_atomic_commit_before_status" and crash_once["armed"]:
            crash_once["armed"] = False
            raise SimulatedCrash(point)

    runtime = LightweightExecutionEngine(store, fault_injector=fault)
    calls = {"n": 0}

    def handler(payload, _context):
        calls["n"] += 1
        return {"value": payload["value"]}

    runtime.register_agent("echo", handler)

    with pytest.raises(SimulatedCrash):
        runtime.invoke(
            AgentRequest(
                request_id="req-crash-after-commit",
                agent_id="echo",
                input={"value": 11},
                execution_policy=make_policy(provider_calls=2),
            )
        )

    task = store.get_task_by_request_id("req-crash-after-commit")
    assert task is not None
    assert task.status == RuntimeStatus.RUNNING
    assert len(store.list_committed_execution_keys(task.task_id)) == 1
    assert len(store.list_checkpoints(task.task_id)) == 1

    resumed = LightweightExecutionEngine(store)
    resumed.register_agent("echo", handler)
    handle = resumed.resume(task.task_id)

    assert handle.status == RuntimeStatus.COMPLETED
    assert calls["n"] == 1
    runs = store.list_runs(task.task_id)
    assert len(runs) == 2
    assert runs[1].resume_of_run_id == runs[0].run_id


def test_g06_atomic_commit_rolls_back_all_terminal_writes_on_fault(tmp_path):
    crash_once = {"armed": True}

    def store_fault(point):
        if point == "after_atomic_writes_before_commit" and crash_once["armed"]:
            crash_once["armed"] = False
            raise SimulatedCrash(point)

    store = SqliteTaskStore(
        tmp_path / "runtime.db",
        fault_injector=store_fault,
    )
    runtime = LightweightExecutionEngine(store)
    calls = {"n": 0}

    def handler(payload, _context):
        calls["n"] += 1
        return {"value": payload["value"]}

    runtime.register_agent("echo", handler)

    with pytest.raises(SimulatedCrash):
        runtime.invoke(
            AgentRequest(
                request_id="req-atomic-fault",
                agent_id="echo",
                input={"value": 9},
                execution_policy=make_policy(provider_calls=2),
            )
        )

    task = store.get_task_by_request_id("req-atomic-fault")
    assert task is not None
    assert len(store.list_committed_execution_keys(task.task_id)) == 0
    assert len(store.list_checkpoints(task.task_id)) == 0

    first_run = store.list_runs(task.task_id)[0]
    first_step = store.list_step_runs(first_run.run_id)[0]
    attempts = store.list_attempts(first_step.step_run_id)
    assert len(attempts) == 1
    assert attempts[0].status == RuntimeStatus.RUNNING

    store.fault_injector = None
    handle = runtime.resume(task.task_id)
    assert handle.status == RuntimeStatus.COMPLETED
    assert calls["n"] == 2
    assert len(store.list_committed_execution_keys(task.task_id)) == 1
    assert len(store.list_checkpoints(task.task_id)) == 1


def test_g03_resume_new_run_skips_committed_step(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls = {"s1": 0, "s2": 0}

    def first(payload, _context):
        calls["s1"] += 1
        return payload["x"] + 1

    def second(_payload, context):
        calls["s2"] += 1
        if calls["s2"] == 1:
            raise RuntimeStepError(
                "temporary execution failure",
                code="TEMPORARY_EXECUTION_FAILURE",
                category=ErrorCategory.EXECUTION,
                retryable=True,
            )
        return context["dependencies"]["s1"] * 10

    runtime.register_agent("a1", first)
    runtime.register_agent("a2", second)

    policy = make_policy(provider_calls=2, step_attempts=1)
    workflow = WorkflowDefinition(
        workflow_id="wf-resume",
        version="1",
        steps=[
            StepDefinition(step_id="s1", agent_id="a1"),
            StepDefinition(step_id="s2", agent_id="a2", depends_on=["s1"]),
        ],
    )

    first_result = runtime.execute(
        WorkflowRequest(
            request_id="req-resume",
            workflow_id="wf-resume",
            input={"x": 2},
            workflow=workflow,
            execution_policy=policy,
        )
    )

    assert first_result.status == RuntimeStatus.PARTIAL
    assert calls == {"s1": 1, "s2": 1}

    handle = runtime.resume(first_result.task_id)
    assert handle.status == RuntimeStatus.COMPLETED
    assert calls == {"s1": 1, "s2": 2}

    runs = store.list_runs(first_result.task_id)
    assert len(runs) == 2
    assert runs[1].run_sequence == 2
    assert runs[1].resume_of_run_id == runs[0].run_id
    assert len(store.list_committed_execution_keys(first_result.task_id)) == 2


def test_r04_hard_provider_call_cap_is_not_multiplied_by_nested_retry(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls = {"n": 0}

    def handler(_payload, _context):
        calls["n"] += 1
        raise RuntimeStepError(
            "temporary transport failure",
            code="TEMP_TRANSPORT",
            category=ErrorCategory.TRANSPORT,
            retryable=True,
        )

    runtime.register_agent("unstable", handler)
    policy = make_policy(
        provider_calls=4,
        step_attempts=3,
        transport=3,
        validation=3,
    )

    result = runtime.invoke(
        AgentRequest(
            request_id="req-hard-cap",
            agent_id="unstable",
            input={"x": 1},
            execution_policy=policy,
        )
    )

    assert result.status == RuntimeStatus.FAILED
    assert result.error is not None
    assert calls["n"] == 4
    assert result.execution.provider_calls == 4
    assert result.execution.retry_budget_exhausted is True


def test_g04_crash_before_provider_call_consumes_no_budget(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    crash_once = {"armed": True}
    calls = {"n": 0}

    def fault(point):
        if point == "before_provider_call" and crash_once["armed"]:
            crash_once["armed"] = False
            raise SimulatedCrash(point)

    def handler(payload, _context):
        calls["n"] += 1
        return payload["value"]

    runtime = LightweightExecutionEngine(store, fault_injector=fault)
    runtime.register_agent("echo", handler)

    with pytest.raises(SimulatedCrash):
        runtime.invoke(
            AgentRequest(
                request_id="req-crash-before-call",
                agent_id="echo",
                input={"value": 5},
                execution_policy=make_policy(provider_calls=1),
            )
        )

    task = store.get_task_by_request_id("req-crash-before-call")
    assert task is not None
    assert calls["n"] == 0
    assert store.count_task_provider_calls(task.task_id) == 0
    assert len(store.list_committed_execution_keys(task.task_id)) == 0

    resumed = LightweightExecutionEngine(store)
    resumed.register_agent("echo", handler)
    handle = resumed.resume(task.task_id)

    assert handle.status == RuntimeStatus.COMPLETED
    assert calls["n"] == 1
    assert store.count_task_provider_calls(task.task_id) == 1


def test_d3_store_migrates_d1_attempt_table_before_index_creation(tmp_path):
    db_path = tmp_path / "runtime.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE runtime_attempt (
                attempt_id TEXT PRIMARY KEY,
                step_run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                record_json TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    SqliteTaskStore(db_path)

    conn = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(runtime_attempt)").fetchall()
        }
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(runtime_attempt)").fetchall()
        }
    finally:
        conn.close()

    assert "execution_key" in columns
    assert "provider_call_seq" in columns
    assert "idx_runtime_attempt_execution_key" in indexes
