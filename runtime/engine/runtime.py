from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel

from runtime.contracts import (
    AgentDefinition,
    AgentRequest,
    AgentResult,
    AttemptRecord,
    AttemptType,
    CheckpointRecord,
    CompletionSummary,
    CompletenessGateResult,
    ErrorCategory,
    ExecutionCommit,
    ExecutionDefinitionSnapshot,
    ExecutionMode,
    ExecutionPolicy,
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
from runtime.reliability import (
    ExistingTaskNotCompleteError,
    ExecutionSnapshotMissingError,
    RetryBudgetExhaustedError,
    RetryCoordinator,
    RuntimeExecutionException,
    RuntimeStepError,
    TaskNotResumableError,
)
from runtime.store import SqliteTaskStore


AgentHandler = Callable[[Any, dict[str, Any]], Any]
FaultInjector = Callable[[str], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_payload(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


_SECRET_KEYS = {
    "secret",
    "client_secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "bearer_token",
    "credential",
    "credentials",
}


def _strip_secrets(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _SECRET_KEYS or normalized.endswith("_secret"):
                continue
            cleaned[key] = _strip_secrets(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_secrets(item) for item in value]
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


def _incomplete_completion(reason: str) -> CompletionSummary:
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
    """P0.3 lightweight engine through D3 Reliability Core.

    Implemented reliability semantics:
    - request_id + request fingerprint idempotency;
    - stable execution_key per Task/Step/partition;
    - persistent provider-call retry budget;
    - atomic execution commit with checkpoint;
    - same Task + new Run resume;
    - committed execution replay suppression;
    - crash-point injection around provider call and atomic commit.
    """

    def __init__(
        self,
        store: SqliteTaskStore,
        *,
        fault_injector: FaultInjector | None = None,
    ):
        self.store = store
        self.fault_injector = fault_injector
        self._agents: dict[str, AgentHandler] = {}
        self._agent_definitions: dict[str, AgentDefinition] = {}
        self._workflows: dict[str, WorkflowDefinition] = {}

    def register_agent(
        self,
        agent_id: str,
        handler: AgentHandler,
        definition: AgentDefinition | None = None,
    ) -> None:
        self._agents[agent_id] = handler
        if definition is not None:
            if definition.agent_id != agent_id:
                raise ValueError("AgentDefinition.agent_id must match registered agent_id")
            self._agent_definitions[agent_id] = definition

    def register_agent_definition(self, definition: AgentDefinition) -> None:
        self._agent_definitions[definition.agent_id] = definition

    def register_workflow(self, definition: WorkflowDefinition) -> None:
        self._workflows[definition.workflow_id] = definition

    def invoke(self, request: AgentRequest) -> AgentResult:
        started = _now()
        task, created = self._create_or_get_task(request, TaskType.AGENT, started)
        if not created:
            snapshot = self.store.get_task_snapshot(task.task_id)
            if isinstance(snapshot.result, AgentResult):
                return snapshot.result
            raise ExistingTaskNotCompleteError(task.task_id, snapshot.status.value)
        return self._invoke_task(
            task,
            request,
            resumed=False,
            resume_of_run_id=None,
        )

    def execute(self, request: WorkflowRequest) -> WorkflowResult:
        started = _now()
        task, created = self._create_or_get_task(request, TaskType.WORKFLOW, started)
        if not created:
            snapshot = self.store.get_task_snapshot(task.task_id)
            if isinstance(snapshot.result, WorkflowResult):
                return snapshot.result
            raise ExistingTaskNotCompleteError(task.task_id, snapshot.status.value)
        return self._execute_task(
            task,
            request,
            resumed=False,
            resume_of_run_id=None,
        )

    def submit(self, request: AgentRequest | WorkflowRequest) -> TaskHandle:
        try:
            if isinstance(request, AgentRequest):
                result = self.invoke(request)
            else:
                result = self.execute(request)
            return TaskHandle(task_id=result.task_id, status=result.status)
        except ExistingTaskNotCompleteError as exc:
            snapshot = self.store.get_task_snapshot(exc.task_id)
            return TaskHandle(task_id=snapshot.task_id, status=snapshot.status)

    def get_task(self, task_id: str) -> TaskSnapshot:
        return self.store.get_task_snapshot(task_id)

    def resume(self, task_id: str) -> TaskHandle:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")

        allowed = {RuntimeStatus.PARTIAL, RuntimeStatus.RUNNING}
        if task.status == RuntimeStatus.WAITING and task.metadata.get("resume_ready") is True:
            allowed.add(RuntimeStatus.WAITING)
        if task.status not in allowed:
            raise TaskNotResumableError(task_id, task.status.value)

        request = self.store.load_request(task_id)
        if request is None:
            raise KeyError(f"request not found for task: {task_id}")
        resume_of = task.current_run_id

        if isinstance(request, AgentRequest):
            result = self._invoke_task(
                task,
                request,
                resumed=True,
                resume_of_run_id=resume_of,
            )
        else:
            result = self._execute_task(
                task,
                request,
                resumed=True,
                resume_of_run_id=resume_of,
            )
        return TaskHandle(task_id=task_id, status=result.status)

    def cancel(self, task_id: str) -> TaskSnapshot:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task.status in {
            RuntimeStatus.COMPLETED,
            RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED,
        }:
            return self.store.get_task_snapshot(task_id)

        requested_at = _now()
        task.cancel_requested = True
        task.cancel_requested_at = task.cancel_requested_at or requested_at
        task.updated_at = requested_at

        if task.status in {
            RuntimeStatus.QUEUED,
            RuntimeStatus.WAITING,
            RuntimeStatus.PARTIAL,
        }:
            task.status = RuntimeStatus.CANCELLED
            task.completed_at = requested_at

        # RUNNING is cooperative: do not kill an in-flight handler/provider call.
        # The execution loop observes cancel_requested at the next safe boundary.
        self.store.save_task(task)
        return self.store.get_task_snapshot(task_id)

    def _sync_cancel_state(self, task: TaskRecord) -> bool:
        latest = self.store.get_task(task.task_id)
        if latest is None:
            return False
        task.cancel_requested = latest.cancel_requested
        task.cancel_requested_at = latest.cancel_requested_at
        return task.cancel_requested

    @staticmethod
    def _cancelled_error(task_id: str) -> RuntimeErrorInfo:
        return RuntimeErrorInfo(
            code="CANCELLED",
            category=ErrorCategory.CANCELLED,
            message=f"task cancellation requested: {task_id}",
            retryable=False,
            details={"task_id": task_id},
        )

    def _resolve_agent_definition(
        self,
        agent_id: str,
        request: AgentRequest | None = None,
    ) -> AgentDefinition:
        current = self._agent_definitions.get(agent_id)
        if current is not None:
            return current
        return AgentDefinition(
            agent_id=agent_id,
            output_schema=request.output_schema if request is not None else None,
        )

    def _build_execution_snapshot(
        self,
        request: AgentRequest | WorkflowRequest,
    ) -> ExecutionDefinitionSnapshot:
        created_at = _now()
        if isinstance(request, AgentRequest):
            definition = self._resolve_agent_definition(request.agent_id, request)
            agent_data = _strip_secrets(definition)
            policy = request.execution_policy or ExecutionPolicy(
                mode=ExecutionMode.SINGLE
            )
            clean_policy = ExecutionPolicy.model_validate(_strip_secrets(policy))
            material = {
                "kind": "AGENT",
                "agent_definition": agent_data,
                "execution_policy": clean_policy.model_dump(mode="json"),
                "output_schema_ref": request.output_schema or definition.output_schema,
                "model_policy": _strip_secrets(clean_policy.model_policy),
            }
            fingerprint = _hash_payload(material)
            snapshot = ExecutionDefinitionSnapshot(
                snapshot_id=f"snapshot-{uuid4().hex}",
                fingerprint=fingerprint,
                agent_definition=agent_data,
                agent_definition_hash=_hash_payload(agent_data),
                prompt_ref=definition.prompt_ref,
                prompt_hash=definition.metadata.get("prompt_hash"),
                output_schema_ref=request.output_schema or definition.output_schema,
                output_schema_version=definition.metadata.get("output_schema_version"),
                output_schema_hash=definition.metadata.get("output_schema_hash"),
                content_strategy_ref=definition.content_strategy_ref,
                content_strategy_version=definition.metadata.get("content_strategy_version"),
                content_strategy_hash=definition.metadata.get("content_strategy_hash"),
                execution_policy=clean_policy,
                model_policy=_strip_secrets(clean_policy.model_policy),
                completeness_gate_ref=definition.metadata.get("completeness_gate_ref"),
                completeness_gate_version=definition.metadata.get("completeness_gate_version"),
                created_at=created_at,
                metadata={"task_type": "AGENT"},
            )
        else:
            definition = request.workflow or self._workflows.get(request.workflow_id)
            if definition is None:
                raise KeyError(f"workflow not found: {request.workflow_id}")
            clean_workflow = WorkflowDefinition.model_validate(
                _strip_secrets(definition)
            )
            policy = request.execution_policy or ExecutionPolicy(
                mode=ExecutionMode.SEQUENTIAL
            )
            clean_policy = ExecutionPolicy.model_validate(_strip_secrets(policy))
            agent_definitions: dict[str, Any] = {}
            for step in clean_workflow.steps:
                agent_definition = self._resolve_agent_definition(step.agent_id)
                agent_definitions[step.agent_id] = _strip_secrets(agent_definition)
            material = {
                "kind": "WORKFLOW",
                "workflow_definition": clean_workflow.model_dump(mode="json"),
                "agent_definitions": agent_definitions,
                "execution_policy": clean_policy.model_dump(mode="json"),
                "model_policy": _strip_secrets(clean_policy.model_policy),
            }
            fingerprint = _hash_payload(material)
            snapshot = ExecutionDefinitionSnapshot(
                snapshot_id=f"snapshot-{uuid4().hex}",
                fingerprint=fingerprint,
                workflow_definition=clean_workflow,
                workflow_definition_hash=_hash_payload(
                    clean_workflow.model_dump(mode="json")
                ),
                execution_policy=clean_policy,
                model_policy=_strip_secrets(clean_policy.model_policy),
                created_at=created_at,
                metadata={
                    "task_type": "WORKFLOW",
                    "agent_definitions": agent_definitions,
                },
            )

        return self.store.save_execution_snapshot(snapshot)

    def _load_execution_snapshot(
        self,
        task: TaskRecord,
    ) -> ExecutionDefinitionSnapshot:
        if not task.execution_snapshot_id:
            raise ExecutionSnapshotMissingError(task.task_id)
        return self.store.get_execution_snapshot(task.execution_snapshot_id)

    def _create_or_get_task(
        self,
        request: AgentRequest | WorkflowRequest,
        task_type: TaskType,
        started: datetime,
    ) -> tuple[TaskRecord, bool]:
        body = request.model_dump(mode="json")
        body_without_request_id = {
            key: value
            for key, value in body.items()
            if key != "request_id"
        }
        request_fingerprint = _hash_payload(body_without_request_id)

        existing = self.store.get_task_by_request_id(request.request_id)
        if existing is not None:
            probe = TaskRecord(
                task_id=f"task-{uuid4().hex}",
                request_id=request.request_id,
                request_fingerprint=request_fingerprint,
                task_type=task_type,
                business_domain=request.metadata.get("business_domain"),
                business_id=request.metadata.get("business_id"),
                status=RuntimeStatus.QUEUED,
                input_hash=_hash_payload(request.input),
                created_at=started,
                updated_at=started,
                metadata=dict(request.metadata),
            )
            return self.store.create_or_get_task(probe, request=request)

        snapshot = self._build_execution_snapshot(request)
        task = TaskRecord(
            task_id=f"task-{uuid4().hex}",
            request_id=request.request_id,
            request_fingerprint=request_fingerprint,
            task_type=task_type,
            business_domain=request.metadata.get("business_domain"),
            business_id=request.metadata.get("business_id"),
            status=RuntimeStatus.QUEUED,
            input_hash=_hash_payload(request.input),
            execution_snapshot_id=snapshot.snapshot_id,
            execution_definition_fingerprint=snapshot.fingerprint,
            created_at=started,
            updated_at=started,
            metadata=dict(request.metadata),
        )
        return self.store.create_or_get_task(task, request=request)

    def _create_run(
        self,
        task: TaskRecord,
        *,
        workflow_id: str | None,
        workflow_version: str | None,
        started: datetime,
        resumed: bool,
        resume_of_run_id: str | None,
    ) -> WorkflowRunRecord:
        if resumed:
            if not resume_of_run_id:
                raise ValueError("resume requires resume_of_run_id")
            run = self.store.create_resume_run(
                task_id=task.task_id,
                resume_of_run_id=resume_of_run_id,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
                input_hash=task.input_hash,
                started_at=started,
            )
        else:
            run = WorkflowRunRecord(
                run_id=f"run-{uuid4().hex}",
                task_id=task.task_id,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
                status=RuntimeStatus.RUNNING,
                input_hash=task.input_hash,
                run_sequence=1,
                execution_snapshot_id=task.execution_snapshot_id,
                started_at=started,
            )
            self.store.save_run(run)

        task.current_run_id = run.run_id
        task.status = RuntimeStatus.RUNNING
        task.started_at = task.started_at or started
        task.updated_at = started
        task.completed_at = None
        self.store.save_task(task)
        return run

    def _invoke_task(
        self,
        task: TaskRecord,
        request: AgentRequest,
        *,
        resumed: bool,
        resume_of_run_id: str | None,
    ) -> AgentResult:
        started = _now()
        run = self._create_run(
            task,
            workflow_id=None,
            workflow_version=None,
            started=started,
            resumed=resumed,
            resume_of_run_id=resume_of_run_id,
        )
        snapshot = self._load_execution_snapshot(task)
        step = StepDefinition(
            step_id=request.agent_id,
            agent_id=request.agent_id,
            execution_policy=snapshot.execution_policy,
        )
        policy = snapshot.execution_policy

        status, data, error, step_run = self._run_step(
            task=task,
            run=run,
            definition=step,
            business_input=request.input,
            context=request.context,
            inherited_policy=policy,
        )

        if self._sync_cancel_state(task):
            status = RuntimeStatus.CANCELLED
            error = self._cancelled_error(task.task_id)

        finished = _now()
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
            duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
            step_attempts=step_run.attempt_count,
            checkpoint_count=len(self.store.list_checkpoints(task.task_id)),
            resumed=resumed,
            trace_id=f"trace-{task.task_id}",
            provider_calls=provider_calls,
            retry_budget_limit=policy.retry_budget.max_provider_calls_per_step,
            retry_budget_consumed=provider_calls,
            retry_budget_exhausted=(
                self.store.count_provider_calls(
                    self._execution_key(task, step)
                )
                >= policy.retry_budget.max_provider_calls_per_step
            ),
            execution_snapshot_id=snapshot.snapshot_id,
            execution_definition_fingerprint=snapshot.fingerprint,
        )
        result = AgentResult(
            task_id=task.task_id,
            run_id=run.run_id,
            request_id=request.request_id,
            agent_id=request.agent_id,
            status=status,
            data=data,
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
                        else "STEP_FAILED"
                    )
                )
            ),
            error=error,
            metadata={"engine": "lightweight-p0", "resumed": resumed},
        )
        self.store.save_task(task, result=result, error=error)
        return result

    def _execute_task(
        self,
        task: TaskRecord,
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

        pending = {step.step_id: step for step in definition.steps}
        completed: set[str] = set()
        step_results: dict[str, StepResultSummary] = {}
        warnings: list[RuntimeWarning] = []
        terminal_error: RuntimeErrorInfo | None = None
        partial_error: RuntimeErrorInfo | None = None
        cancelled = False

        inherited_policy = snapshot.execution_policy
        mode = inherited_policy.mode

        while pending:
            if self._sync_cancel_state(task):
                cancelled = True
                pending.clear()
                break

            ready = [
                step
                for step in pending.values()
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
                            inherited_policy,
                        )
                        for step in ready
                    }
                    wave = [
                        (
                            next(s for s in ready if s.step_id == step_id),
                            future.result(),
                        )
                        for step_id, future in futures.items()
                    ]
            else:
                selected = ready[:1] if mode == ExecutionMode.SEQUENTIAL else ready
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

                if status == RuntimeStatus.CANCELLED:
                    cancelled = True
                    pending.clear()
                    break

                if step.required_for_completion:
                    if status == RuntimeStatus.PARTIAL:
                        partial_error = error
                    else:
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
                        code="OPTIONAL_STEP_INCOMPLETE",
                        message=f"optional step incomplete: {step.step_id}",
                        details={"status": status.value},
                    )
                )
                completed.add(step.step_id)

            if self._sync_cancel_state(task):
                cancelled = True
                pending.clear()
                break

            if terminal_error or partial_error:
                break

        finished = _now()
        if cancelled:
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
            duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
            step_attempts=provider_calls,
            checkpoint_count=len(self.store.list_checkpoints(task.task_id)),
            resumed=resumed,
            trace_id=f"trace-{task.task_id}",
            provider_calls=provider_calls,
            retry_budget_limit=inherited_policy.retry_budget.max_provider_calls_per_task,
            retry_budget_consumed=provider_calls,
            retry_budget_exhausted=(
                inherited_policy.retry_budget.max_provider_calls_per_task is not None
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
        )
        self.store.save_task(task, result=result, error=error)
        return result

    def _execution_key(
        self,
        task: TaskRecord,
        definition: StepDefinition,
    ) -> str:
        partition_key = task.metadata.get("partition_key") or "__default__"
        digest = _hash_payload(
            {
                "task_id": task.task_id,
                "step_id": definition.step_id,
                "partition_key": partition_key,
            }
        )
        return f"exec-{digest}"

    @staticmethod
    def _attempt_type(error: RuntimeErrorInfo | None) -> AttemptType:
        if error is None:
            return AttemptType.STEP
        if error.category == ErrorCategory.TRANSPORT:
            return AttemptType.TRANSPORT
        if error.category == ErrorCategory.VALIDATION:
            return AttemptType.VALIDATION
        return AttemptType.STEP

    @staticmethod
    def _error_info(exc: Exception) -> RuntimeErrorInfo:
        if isinstance(exc, RuntimeExecutionException):
            return exc.as_error_info()
        return RuntimeErrorInfo(
            code="STEP_EXECUTION_FAILED",
            category=ErrorCategory.EXECUTION,
            message=str(exc),
            retryable=False,
            details={},
        )

    def _inject(self, point: str) -> None:
        if self.fault_injector:
            self.fault_injector(point)

    def _run_step(
        self,
        task: TaskRecord,
        run: WorkflowRunRecord,
        definition: StepDefinition,
        business_input: Any,
        context: dict[str, Any],
        inherited_policy: ExecutionPolicy,
    ) -> tuple[RuntimeStatus, Any, RuntimeErrorInfo | None, StepRunRecord]:
        started = _now()
        policy = definition.execution_policy or inherited_policy
        execution_key = self._execution_key(task, definition)

        step_run = StepRunRecord(
            step_run_id=f"step-{uuid4().hex}",
            run_id=run.run_id,
            step_id=definition.step_id,
            agent_id=definition.agent_id,
            status=RuntimeStatus.RUNNING,
            attempt_count=0,
            input_hash=_hash_payload({"input": business_input, "context": context}),
            started_at=started,
            metadata={"execution_key": execution_key},
        )
        self.store.save_step_run(step_run)

        committed = self.store.get_committed_execution(execution_key)
        if committed is not None:
            step_run.status = RuntimeStatus.COMPLETED
            step_run.completed_at = _now()
            step_run.output_ref = f"commit:{committed.commit_id}"
            step_run.metadata["reused_committed_execution"] = True
            self.store.save_step_run(step_run)
            return (
                RuntimeStatus.COMPLETED,
                committed.result_data,
                None,
                step_run,
            )

        step_limit = max(
            1,
            min(
                policy.step_retry.max_attempts,
                policy.retry_budget.max_step_attempts,
            ),
        )
        validation_limit = max(
            1,
            min(
                policy.validation_retry.max_attempts,
                policy.retry_budget.max_validation_cycles_per_step_attempt,
            ),
        )
        transport_limit = max(
            1,
            min(
                policy.transport_retry.max_attempts,
                policy.retry_budget.max_transport_attempts_per_model_call,
            ),
        )

        last_error: RuntimeErrorInfo | None = None
        local_calls = 0
        hard_budget_exhausted = False

        for step_attempt_no in range(1, step_limit + 1):
            retry_step = False
            for validation_cycle_no in range(1, validation_limit + 1):
                retry_validation = False
                for transport_attempt_no in range(1, transport_limit + 1):
                    current_task = self.store.get_task(task.task_id)
                    if current_task is not None and current_task.cancel_requested:
                        cancel_error = self._cancelled_error(task.task_id)
                        step_run.status = RuntimeStatus.CANCELLED
                        step_run.error = cancel_error
                        step_run.completed_at = _now()
                        self.store.save_step_run(step_run)
                        return (
                            RuntimeStatus.CANCELLED,
                            None,
                            cancel_error,
                            step_run,
                        )

                    consumed = self.store.count_provider_calls(execution_key)
                    task_consumed = self.store.count_task_provider_calls(task.task_id)
                    try:
                        RetryCoordinator.reserve_provider_call(
                            policy,
                            execution_key=execution_key,
                            consumed=consumed,
                        )
                        task_limit = policy.retry_budget.max_provider_calls_per_task
                        if task_limit is not None and task_consumed >= task_limit:
                            raise RetryBudgetExhaustedError(
                                execution_key,
                                task_consumed,
                                task_limit,
                            )
                    except RetryBudgetExhaustedError as exc:
                        last_error = exc.as_error_info()
                        hard_budget_exhausted = True
                        break

                    self._inject("before_provider_call")

                    provider_call_seq = consumed + 1
                    attempt = AttemptRecord(
                        attempt_id=f"attempt-{uuid4().hex}",
                        step_run_id=step_run.step_run_id,
                        attempt_no=provider_call_seq,
                        attempt_type=AttemptType.STEP,
                        status=RuntimeStatus.RUNNING,
                        execution_key=execution_key,
                        replayed_after_crash=self.store.has_incomplete_attempt(
                            execution_key
                        ),
                        step_attempt_no=step_attempt_no,
                        validation_cycle_no=validation_cycle_no,
                        transport_attempt_no=transport_attempt_no,
                        provider_call_seq=provider_call_seq,
                        started_at=_now(),
                    )
                    self.store.save_attempt(attempt)
                    local_calls += 1
                    step_run.attempt_count = local_calls
                    self.store.save_step_run(step_run)

                    try:
                        handler = self._agents[definition.agent_id]
                        handler_context = {
                            **context,
                            "runtime": {
                                "task_id": task.task_id,
                                "run_id": run.run_id,
                                "step_run_id": step_run.step_run_id,
                                "execution_key": execution_key,
                                "provider_call_seq": provider_call_seq,
                                "replayed_after_crash": attempt.replayed_after_crash,
                                "execution_snapshot_id": task.execution_snapshot_id,
                                "execution_definition_fingerprint": task.execution_definition_fingerprint,
                                "sdk_retry_policy": {
                                    "implicit_retry_enabled": False,
                                    "adapter_must_report_actual_provider_requests": True,
                                },
                            },
                        }
                        snapshot = self._load_execution_snapshot(task)
                        if snapshot.workflow_definition is not None:
                            agent_snapshot = snapshot.metadata.get(
                                "agent_definitions", {}
                            ).get(definition.agent_id)
                        else:
                            agent_snapshot = snapshot.agent_definition
                        handler_context["runtime"]["agent_definition"] = agent_snapshot
                        handler_context["runtime"]["model_policy"] = snapshot.model_policy
                        value = _normalize_data(
                            handler(business_input, handler_context)
                        )
                    except Exception as exc:
                        finished = _now()
                        error = self._error_info(exc)
                        last_error = error
                        attempt.status = RuntimeStatus.FAILED
                        attempt.attempt_type = self._attempt_type(error)
                        attempt.trigger_error_category = error.category.value
                        attempt.error = error
                        attempt.completed_at = finished
                        self.store.save_attempt(attempt)

                        if not error.retryable:
                            retry_step = False
                            retry_validation = False
                            break

                        if (
                            error.category == ErrorCategory.TRANSPORT
                            and transport_attempt_no < transport_limit
                        ):
                            continue

                        if (
                            error.category == ErrorCategory.VALIDATION
                            and validation_cycle_no < validation_limit
                        ):
                            retry_validation = True
                        else:
                            retry_step = True
                        break

                    self._inject("after_provider_return_before_commit")

                    finished = _now()
                    attempt.status = RuntimeStatus.COMPLETED
                    attempt.completed_at = finished
                    step_run.status = RuntimeStatus.COMPLETED
                    step_run.completed_at = finished
                    commit_id = f"commit-{uuid4().hex}"
                    step_run.output_ref = f"commit:{commit_id}"

                    checkpoint = CheckpointRecord(
                        checkpoint_id=f"checkpoint-{uuid4().hex}",
                        task_id=task.task_id,
                        run_id=run.run_id,
                        step_run_id=step_run.step_run_id,
                        input_hash=step_run.input_hash,
                        status=RuntimeStatus.COMPLETED,
                        result_ref=f"commit:{commit_id}",
                        execution_key=execution_key,
                        commit_id=commit_id,
                        checkpoint_sequence=(
                            len(self.store.list_checkpoints(task.task_id)) + 1
                        ),
                        partition_key=task.metadata.get("partition_key"),
                        created_at=finished,
                    )
                    commit = ExecutionCommit(
                        commit_id=commit_id,
                        task_id=task.task_id,
                        run_id=run.run_id,
                        step_run=step_run,
                        attempt=attempt,
                        checkpoint=checkpoint,
                        execution_key=execution_key,
                        result_data=value,
                        status=RuntimeStatus.COMPLETED,
                    )
                    commit_result = self.store.commit_execution_progress(commit)
                    if not commit_result.inserted:
                        existing = self.store.get_committed_execution(
                            execution_key
                        )
                        if existing is not None:
                            value = existing.result_data
                            step_run.status = RuntimeStatus.COMPLETED
                            step_run.completed_at = finished
                            step_run.output_ref = f"commit:{existing.commit_id}"
                            step_run.metadata["reused_committed_execution"] = True
                            self.store.save_step_run(step_run)

                    self._inject("after_atomic_commit_before_status")
                    return RuntimeStatus.COMPLETED, value, None, step_run

                if hard_budget_exhausted:
                    break
                if last_error and not last_error.retryable:
                    break
                if retry_validation:
                    continue
                if retry_step:
                    break
                if last_error is not None:
                    break

            if hard_budget_exhausted:
                break
            if last_error and not last_error.retryable:
                break
            if retry_step and step_attempt_no < step_limit:
                continue
            break

        consumed = self.store.count_provider_calls(execution_key)
        if consumed >= policy.retry_budget.max_provider_calls_per_step:
            hard_budget_exhausted = True

        if last_error is None:
            last_error = RuntimeErrorInfo(
                code="STEP_EXECUTION_INCOMPLETE",
                category=ErrorCategory.EXECUTION,
                message=f"step did not produce a committed result: {definition.step_id}",
                retryable=not hard_budget_exhausted,
                details={"execution_key": execution_key},
            )

        if (
            hard_budget_exhausted
            and self.store.has_committed_progress(task.task_id)
        ):
            status = RuntimeStatus.PARTIAL
        else:
            status = RetryCoordinator.failure_status(
                retryable=last_error.retryable,
                hard_budget_exhausted=hard_budget_exhausted,
            )
        finished = _now()
        step_run.status = status
        step_run.error = last_error
        step_run.completed_at = finished
        self.store.save_step_run(step_run)
        return status, None, last_error, step_run


class AgentRuntime(LightweightExecutionEngine):
    pass
