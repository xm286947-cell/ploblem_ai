from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel

from runtime.contracts import (
    AgentRequest,
    AgentResult,
    AttemptRecord,
    AttemptType,
    CompletionSummary,
    CompletenessGateResult,
    ErrorCategory,
    ExecutionMode,
    ExecutionSummary,
    RuntimeErrorInfo,
    RuntimeStatus,
    RuntimeWarning,
    StepDefinition,
    StepResultSummary,
    StepRunRecord,
    TaskHandle,
    TaskRecord,
    TaskSnapshot,
    TaskType,
    WorkflowDefinition,
    WorkflowRequest,
    WorkflowResult,
    WorkflowRunRecord,
)
from runtime.store import SqliteTaskStore


AgentHandler = Callable[[Any, dict[str, Any]], Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_payload(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _success_completion() -> CompletionSummary:
    return CompletionSummary(
        gate=CompletenessGateResult(
            source_coverage_complete=None,
            schema_valid=True,
            merge_complete=True,
            evidence_integrity=True,
            business_gate_passed=None,
            passed=True,
            reasons=[],
        ),
        business_consumable=True,
        incomplete_reasons=[],
    )


def _failed_completion(reason: str) -> CompletionSummary:
    return CompletionSummary(
        gate=CompletenessGateResult(
            source_coverage_complete=None,
            schema_valid=False,
            merge_complete=False,
            evidence_integrity=True,
            business_gate_passed=None,
            passed=False,
            reasons=[reason],
        ),
        business_consumable=False,
        incomplete_reasons=[reason],
    )


class LightweightExecutionEngine:
    """D1/D2 execution core for P0.3.

    The engine intentionally implements only the current stage:
    canonical contracts, persistent Task/Run/Step/Attempt records, and
    SINGLE/SEQUENTIAL/PARALLEL execution. Retry/Resume/atomic commit are D3.
    """

    def __init__(self, store: SqliteTaskStore):
        self.store = store
        self._agents: dict[str, AgentHandler] = {}
        self._workflows: dict[str, WorkflowDefinition] = {}

    def register_agent(self, agent_id: str, handler: AgentHandler) -> None:
        self._agents[agent_id] = handler

    def register_workflow(self, definition: WorkflowDefinition) -> None:
        self._workflows[definition.workflow_id] = definition

    def invoke(self, request: AgentRequest) -> AgentResult:
        started = _now()
        task = self._create_task(request, TaskType.AGENT, started)
        run = self._create_run(task, workflow_id=None, workflow_version=None, started=started)
        step = StepDefinition(step_id=request.agent_id, agent_id=request.agent_id)

        status, data, error, step_run = self._run_step(
            task=task,
            run=run,
            definition=step,
            business_input=request.input,
            context=request.context,
        )

        finished = _now()
        run.status = status
        run.completed_at = finished
        self.store.save_run(run)

        task.status = status
        task.updated_at = finished
        task.completed_at = finished
        self.store.save_task(task)

        execution = ExecutionSummary(
            started_at=started,
            completed_at=finished,
            duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
            step_attempts=step_run.attempt_count,
            trace_id=f"trace-{task.task_id}",
        )
        result = AgentResult(
            task_id=task.task_id,
            run_id=run.run_id,
            request_id=request.request_id,
            agent_id=request.agent_id,
            status=status,
            data=data,
            execution=execution,
            completion=_success_completion() if status == RuntimeStatus.COMPLETED else _failed_completion("STEP_FAILED"),
            error=error,
            metadata={"engine": "lightweight-p0"},
        )
        self.store.save_task(task, result=result, error=error)
        return result

    def execute(self, request: WorkflowRequest) -> WorkflowResult:
        definition = request.workflow or self._workflows.get(request.workflow_id)
        if definition is None:
            raise KeyError(f"workflow not found: {request.workflow_id}")

        started = _now()
        task = self._create_task(request, TaskType.WORKFLOW, started)
        run = self._create_run(
            task,
            workflow_id=definition.workflow_id,
            workflow_version=definition.version,
            started=started,
        )

        pending = {step.step_id: step for step in definition.steps}
        completed: set[str] = set()
        step_results: dict[str, StepResultSummary] = {}
        warnings: list[RuntimeWarning] = []
        terminal_error: RuntimeErrorInfo | None = None

        mode = (request.execution_policy.mode if request.execution_policy else ExecutionMode.SEQUENTIAL)

        while pending:
            ready = [
                step for step in pending.values()
                if all(dep in completed for dep in step.depends_on)
            ]
            if not ready:
                terminal_error = RuntimeErrorInfo(
                    code="WORKFLOW_DEPENDENCY_DEADLOCK",
                    category=ErrorCategory.EXECUTION,
                    message="workflow has unresolved or cyclic dependencies",
                    retryable=False,
                    details={"pending_steps": sorted(pending)},
                )
                break

            if mode == ExecutionMode.PARALLEL and len(ready) > 1:
                max_workers = min(len(ready), 8)
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
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
                        )
                        for step in ready
                    }
                    wave = [(next(s for s in ready if s.step_id == step_id), future.result())
                            for step_id, future in futures.items()]
            else:
                wave = []
                for step in ready[:1] if mode == ExecutionMode.SEQUENTIAL else ready:
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
                            ),
                        )
                    )

            for step, (status, data, error, _step_run) in wave:
                step_results[step.step_id] = StepResultSummary(
                    step_id=step.step_id,
                    agent_id=step.agent_id,
                    status=status,
                    data=data,
                    error=error,
                )
                pending.pop(step.step_id, None)
                if status == RuntimeStatus.COMPLETED:
                    completed.add(step.step_id)
                    continue
                if step.required_for_completion:
                    terminal_error = error or RuntimeErrorInfo(
                        code="REQUIRED_STEP_FAILED",
                        category=ErrorCategory.EXECUTION,
                        message=f"required step failed: {step.step_id}",
                        retryable=False,
                    )
                    pending.clear()
                    break
                warnings.append(
                    RuntimeWarning(
                        code="OPTIONAL_STEP_FAILED",
                        message=f"optional step failed: {step.step_id}",
                    )
                )
                completed.add(step.step_id)

            if terminal_error:
                break

        finished = _now()
        status = RuntimeStatus.FAILED if terminal_error else RuntimeStatus.COMPLETED
        run.status = status
        run.completed_at = finished
        run.error = terminal_error
        self.store.save_run(run)

        task.status = status
        task.updated_at = finished
        task.completed_at = finished
        self.store.save_task(task, error=terminal_error)

        execution = ExecutionSummary(
            started_at=started,
            completed_at=finished,
            duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
            step_attempts=sum(1 for _ in step_results),
            trace_id=f"trace-{task.task_id}",
        )
        data = {step_id: item.data for step_id, item in step_results.items() if item.status == RuntimeStatus.COMPLETED}
        result = WorkflowResult(
            task_id=task.task_id,
            run_id=run.run_id,
            request_id=request.request_id,
            workflow_id=definition.workflow_id,
            status=status,
            data=data,
            step_results=step_results,
            execution=execution,
            completion=_success_completion() if status == RuntimeStatus.COMPLETED else _failed_completion("WORKFLOW_FAILED"),
            warnings=warnings,
            error=terminal_error,
        )
        self.store.save_task(task, result=result, error=terminal_error)
        return result

    def submit(self, request: AgentRequest | WorkflowRequest) -> TaskHandle:
        if isinstance(request, AgentRequest):
            result = self.invoke(request)
        else:
            result = self.execute(request)
        return TaskHandle(task_id=result.task_id, status=result.status)

    def get_task(self, task_id: str) -> TaskSnapshot:
        return self.store.get_task_snapshot(task_id)

    def resume(self, task_id: str) -> TaskHandle:
        raise NotImplementedError("resume is implemented in D3 Reliability Core")

    def cancel(self, task_id: str) -> TaskSnapshot:
        snapshot = self.store.get_task_snapshot(task_id)
        if snapshot.status in {RuntimeStatus.COMPLETED, RuntimeStatus.FAILED, RuntimeStatus.CANCELLED}:
            return snapshot
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        task.status = RuntimeStatus.CANCELLED
        task.updated_at = _now()
        task.completed_at = task.updated_at
        self.store.save_task(task)
        return self.store.get_task_snapshot(task_id)

    def _create_task(
        self,
        request: AgentRequest | WorkflowRequest,
        task_type: TaskType,
        started: datetime,
    ) -> TaskRecord:
        body = request.model_dump(mode="json")
        body_without_request_id = {k: v for k, v in body.items() if k != "request_id"}
        task = TaskRecord(
            task_id=f"task-{uuid4().hex}",
            request_id=request.request_id,
            request_fingerprint=_hash_payload(body_without_request_id),
            task_type=task_type,
            business_domain=request.metadata.get("business_domain"),
            business_id=request.metadata.get("business_id"),
            status=RuntimeStatus.QUEUED,
            input_hash=_hash_payload(request.input),
            created_at=started,
            updated_at=started,
            metadata=dict(request.metadata),
        )
        self.store.save_task(task, request=request)
        task.status = RuntimeStatus.RUNNING
        task.started_at = started
        task.updated_at = started
        self.store.save_task(task)
        return task

    def _create_run(
        self,
        task: TaskRecord,
        workflow_id: str | None,
        workflow_version: str | None,
        started: datetime,
    ) -> WorkflowRunRecord:
        run = WorkflowRunRecord(
            run_id=f"run-{uuid4().hex}",
            task_id=task.task_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            status=RuntimeStatus.RUNNING,
            input_hash=task.input_hash,
            run_sequence=1,
            started_at=started,
        )
        self.store.save_run(run)
        task.current_run_id = run.run_id
        task.updated_at = started
        self.store.save_task(task)
        return run

    def _run_step(
        self,
        task: TaskRecord,
        run: WorkflowRunRecord,
        definition: StepDefinition,
        business_input: Any,
        context: dict[str, Any],
    ) -> tuple[RuntimeStatus, Any, RuntimeErrorInfo | None, StepRunRecord]:
        started = _now()
        step_run = StepRunRecord(
            step_run_id=f"step-{uuid4().hex}",
            run_id=run.run_id,
            step_id=definition.step_id,
            agent_id=definition.agent_id,
            status=RuntimeStatus.RUNNING,
            attempt_count=1,
            input_hash=_hash_payload({"input": business_input, "context": context}),
            started_at=started,
        )
        self.store.save_step_run(step_run)

        attempt = AttemptRecord(
            attempt_id=f"attempt-{uuid4().hex}",
            step_run_id=step_run.step_run_id,
            attempt_no=1,
            attempt_type=AttemptType.STEP,
            status=RuntimeStatus.RUNNING,
            started_at=started,
        )
        self.store.save_attempt(attempt)

        try:
            handler = self._agents[definition.agent_id]
            value = _normalize_data(handler(business_input, context))
            finished = _now()
            attempt.status = RuntimeStatus.COMPLETED
            attempt.completed_at = finished
            step_run.status = RuntimeStatus.COMPLETED
            step_run.completed_at = finished
            step_run.output_ref = f"inline:{step_run.step_run_id}"
            self.store.save_attempt(attempt)
            self.store.save_step_run(step_run)
            return RuntimeStatus.COMPLETED, value, None, step_run
        except Exception as exc:
            finished = _now()
            error = RuntimeErrorInfo(
                code="STEP_EXECUTION_FAILED",
                category=ErrorCategory.EXECUTION,
                message=str(exc),
                retryable=False,
                details={"step_id": definition.step_id, "agent_id": definition.agent_id},
            )
            attempt.status = RuntimeStatus.FAILED
            attempt.error = error
            attempt.completed_at = finished
            step_run.status = RuntimeStatus.FAILED
            step_run.error = error
            step_run.completed_at = finished
            self.store.save_attempt(attempt)
            self.store.save_step_run(step_run)
            return RuntimeStatus.FAILED, None, error, step_run


class AgentRuntime(LightweightExecutionEngine):
    pass
