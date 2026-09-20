from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from runtime.contracts import (
    ErrorCategory,
    ExecutionMode,
    ExecutionSummary,
    RuntimeErrorInfo,
    RuntimeStatus,
    RuntimeWarning,
    StepResultSummary,
    WorkflowRequest,
    WorkflowResult,
)
from runtime.reliability import ExecutionSnapshotMissingError

from .runtime import (
    LightweightExecutionEngine,
    _incomplete_completion,
    _now,
    _success_completion,
)


class _GraphState(TypedDict):
    pending: list[str]
    completed: list[str]
    step_results: dict[str, dict[str, Any]]
    warnings: list[dict[str, Any]]
    terminal_error: dict[str, Any] | None
    partial_error: dict[str, Any] | None
    cancelled: bool
    iteration: int


class LangGraphExecutionEngine(LightweightExecutionEngine):
    """LangGraph-backed workflow scheduler using the canonical Runtime state.

    Contract, TaskStore, Retry, execution_key, atomic commit, checkpoints,
    snapshots, resume and cancellation remain owned by the Runtime. LangGraph
    replaces only the workflow scheduling loop so D10 can measure framework
    substitution without creating a second durable-state authority.
    """

    engine_name = "langgraph-p0"

    def _execute_task(
        self,
        task,
        request: WorkflowRequest,
        *,
        resumed: bool,
        resume_of_run_id: str | None,
    ) -> WorkflowResult:
        snapshot = self._load_execution_snapshot(task)
        definition = snapshot.workflow_definition
        if definition is None:
            raise ExecutionSnapshotMissingError(task.task_id)

        started = _now()
        run = self._create_run(
            task,
            workflow_id=definition.workflow_id,
            workflow_version=definition.version,
            started=started,
            resumed=resumed,
            resume_of_run_id=resume_of_run_id,
        )
        inherited_policy = snapshot.execution_policy
        mode = inherited_policy.mode
        steps = {step.step_id: step for step in definition.steps}

        def execute_wave(state: _GraphState) -> _GraphState:
            pending = list(state["pending"])
            completed = set(state["completed"])
            step_results = {
                key: StepResultSummary.model_validate(value)
                for key, value in state["step_results"].items()
            }
            warnings = [
                RuntimeWarning.model_validate(item)
                for item in state["warnings"]
            ]
            terminal_error = (
                RuntimeErrorInfo.model_validate(state["terminal_error"])
                if state["terminal_error"]
                else None
            )
            partial_error = (
                RuntimeErrorInfo.model_validate(state["partial_error"])
                if state["partial_error"]
                else None
            )
            cancelled = bool(state["cancelled"])

            if terminal_error or partial_error or cancelled or not pending:
                return state

            if self._sync_cancel_state(task):
                cancelled = True
                pending = []
            else:
                ready = [
                    steps[step_id]
                    for step_id in pending
                    if all(dep in completed for dep in steps[step_id].depends_on)
                ]
                if not ready:
                    terminal_error = RuntimeErrorInfo(
                        code="WORKFLOW_DEPENDENCY_DEADLOCK",
                        category=ErrorCategory.EXECUTION,
                        message="workflow has unresolved or cyclic dependencies",
                        retryable=False,
                        details={"pending_steps": sorted(pending)},
                    )
                    pending = []
                else:
                    if mode == ExecutionMode.PARALLEL and len(ready) > 1:
                        with ThreadPoolExecutor(
                            max_workers=min(len(ready), 8)
                        ) as pool:
                            futures = {
                                step.step_id: pool.submit(
                                    self._run_step,
                                    task,
                                    run,
                                    step,
                                    request.input,
                                    {
                                        **request.context,
                                        "dependencies": {
                                            dep: step_results[dep].data
                                            for dep in step.depends_on
                                            if dep in step_results
                                        },
                                    },
                                    inherited_policy,
                                )
                                for step in ready
                            }
                            wave = [
                                (
                                    steps[step_id],
                                    future.result(),
                                )
                                for step_id, future in futures.items()
                            ]
                    else:
                        selected = (
                            ready[:1]
                            if mode == ExecutionMode.SEQUENTIAL
                            else ready
                        )
                        wave = []
                        for step in selected:
                            wave.append(
                                (
                                    step,
                                    self._run_step(
                                        task=task,
                                        run=run,
                                        definition=step,
                                        business_input=request.input,
                                        context={
                                            **request.context,
                                            "dependencies": {
                                                dep: step_results[dep].data
                                                for dep in step.depends_on
                                                if dep in step_results
                                            },
                                        },
                                        inherited_policy=inherited_policy,
                                    ),
                                )
                            )

                    for step, (
                        status,
                        data,
                        error,
                        _step_run,
                    ) in wave:
                        step_results[step.step_id] = StepResultSummary(
                            step_id=step.step_id,
                            agent_id=step.agent_id,
                            status=status,
                            data=data,
                            error=error,
                        )
                        if step.step_id in pending:
                            pending.remove(step.step_id)

                        if status == RuntimeStatus.COMPLETED:
                            completed.add(step.step_id)
                            continue

                        if status == RuntimeStatus.CANCELLED:
                            cancelled = True
                            pending = []
                            break

                        if step.required_for_completion:
                            if status == RuntimeStatus.PARTIAL:
                                partial_error = error
                            else:
                                terminal_error = error or RuntimeErrorInfo(
                                    code="REQUIRED_STEP_FAILED",
                                    category=ErrorCategory.EXECUTION,
                                    message=(
                                        "required step failed: "
                                        f"{step.step_id}"
                                    ),
                                    retryable=False,
                                )
                            pending = []
                            break

                        warnings.append(
                            RuntimeWarning(
                                code="OPTIONAL_STEP_INCOMPLETE",
                                message=(
                                    "optional step incomplete: "
                                    f"{step.step_id}"
                                ),
                                details={"status": status.value},
                            )
                        )
                        completed.add(step.step_id)

                    if self._sync_cancel_state(task):
                        cancelled = True
                        pending = []

            return _GraphState(
                pending=pending,
                completed=sorted(completed),
                step_results={
                    key: value.model_dump(mode="json")
                    for key, value in step_results.items()
                },
                warnings=[
                    item.model_dump(mode="json")
                    for item in warnings
                ],
                terminal_error=(
                    terminal_error.model_dump(mode="json")
                    if terminal_error
                    else None
                ),
                partial_error=(
                    partial_error.model_dump(mode="json")
                    if partial_error
                    else None
                ),
                cancelled=cancelled,
                iteration=int(state["iteration"]) + 1,
            )

        def route(state: _GraphState) -> str:
            if (
                state["pending"]
                and not state["terminal_error"]
                and not state["partial_error"]
                and not state["cancelled"]
            ):
                return "continue"
            return "done"

        builder = StateGraph(_GraphState)
        builder.add_node("execute_wave", execute_wave)
        builder.add_edge(START, "execute_wave")
        builder.add_conditional_edges(
            "execute_wave",
            route,
            {
                "continue": "execute_wave",
                "done": END,
            },
        )
        graph = builder.compile()

        final_state = graph.invoke(
            _GraphState(
                pending=[step.step_id for step in definition.steps],
                completed=[],
                step_results={},
                warnings=[],
                terminal_error=None,
                partial_error=None,
                cancelled=False,
                iteration=0,
            )
        )

        step_results = {
            key: StepResultSummary.model_validate(value)
            for key, value in final_state["step_results"].items()
        }
        warnings = [
            RuntimeWarning.model_validate(item)
            for item in final_state["warnings"]
        ]
        terminal_error = (
            RuntimeErrorInfo.model_validate(final_state["terminal_error"])
            if final_state["terminal_error"]
            else None
        )
        partial_error = (
            RuntimeErrorInfo.model_validate(final_state["partial_error"])
            if final_state["partial_error"]
            else None
        )

        finished = _now()
        if final_state["cancelled"]:
            status = RuntimeStatus.CANCELLED
            error = self._cancelled_error(task.task_id)
        elif terminal_error:
            status = RuntimeStatus.FAILED
            error = terminal_error
        elif partial_error:
            status = RuntimeStatus.PARTIAL
            error = partial_error
        else:
            status = RuntimeStatus.COMPLETED
            error = None

        run.status = status
        run.completed_at = finished
        run.error = error
        self.store.save_run(run)

        task.status = status
        task.updated_at = finished
        task.completed_at = finished
        self.store.save_task(task, error=error)

        provider_calls = self.store.count_task_provider_calls(task.task_id)
        execution = ExecutionSummary(
            started_at=started,
            completed_at=finished,
            duration_ms=max(
                0,
                int((finished - started).total_seconds() * 1000),
            ),
            step_attempts=provider_calls,
            checkpoint_count=len(
                self.store.list_checkpoints(task.task_id)
            ),
            resumed=resumed,
            trace_id=f"trace-{task.task_id}",
            provider_calls=provider_calls,
            retry_budget_limit=(
                inherited_policy.retry_budget.max_provider_calls_per_task
            ),
            retry_budget_consumed=provider_calls,
            retry_budget_exhausted=(
                inherited_policy.retry_budget.max_provider_calls_per_task
                is not None
                and provider_calls
                >= inherited_policy.retry_budget.max_provider_calls_per_task
            ),
            execution_snapshot_id=snapshot.snapshot_id,
            execution_definition_fingerprint=snapshot.fingerprint,
        )
        data = {
            step_id: item.data
            for step_id, item in step_results.items()
            if item.status == RuntimeStatus.COMPLETED
        }
        result = WorkflowResult(
            task_id=task.task_id,
            run_id=run.run_id,
            request_id=request.request_id,
            workflow_id=definition.workflow_id,
            status=status,
            data=data,
            step_results=step_results,
            execution=execution,
            completion=(
                _success_completion()
                if status == RuntimeStatus.COMPLETED
                else _incomplete_completion(
                    "RETRYABLE_INCOMPLETE"
                    if status == RuntimeStatus.PARTIAL
                    else (
                        "CANCELLED"
                        if status == RuntimeStatus.CANCELLED
                        else "WORKFLOW_FAILED"
                    )
                )
            ),
            warnings=warnings,
            error=error,
            metadata={
                "engine": self.engine_name,
                "graph_iterations": final_state["iteration"],
            },
        )
        self.store.save_task(task, result=result, error=error)
        return result
