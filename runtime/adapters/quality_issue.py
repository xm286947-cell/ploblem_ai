from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from runtime.contracts import (
    AgentDefinition,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    LegacyProjectionBinding,
    ProjectionOutboxEvent,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    StepDefinition,
    TaskSnapshot,
    WorkflowDefinition,
    WorkflowRequest,
    WorkflowResult,
)
from runtime.engine import LightweightExecutionEngine
from runtime.store import SqliteTaskStore


QUALITY_ISSUE_STAGES = (
    "occurrence",
    "escape",
    "recurrence",
    "capability_gap",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _legacy_analysis_set_id(run_id: str) -> str:
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    return f"LAS-{digest}"


class QualityIssueModelYamlAdapter:
    """Translate the existing model.yaml into frozen Runtime definitions."""

    def __init__(
        self,
        root: str | Path,
        *,
        agent_id: str = "DEFAULT",
    ):
        self.root = Path(root)
        self.agent_id = str(agent_id or "DEFAULT")

    def build(
        self,
    ) -> tuple[dict[str, AgentDefinition], WorkflowDefinition, ExecutionPolicy]:
        from quality_knowledge.model_config import load_quality_issue_ai_config

        cfg, config_path = load_quality_issue_ai_config(
            self.root,
            agent_id=self.agent_id,
        )
        selected_agent = str(cfg.get("_agent_id") or "DEFAULT")
        stage_runtime = dict(cfg.get("stage_runtime") or {})

        definitions: dict[str, AgentDefinition] = {}
        steps: list[StepDefinition] = []

        previous_stage: str | None = None
        for stage in QUALITY_ISSUE_STAGES:
            stage_cfg = {**cfg, **dict(stage_runtime.get(stage) or {})}
            transport_attempts = max(1, int(stage_cfg.get("max_retries", 0)) + 1)
            validation_attempts = max(
                1,
                int(stage_cfg.get("validation_retries", 0)) + 1,
            )
            provider_budget = max(
                1,
                transport_attempts * validation_attempts,
            )
            execution_policy = ExecutionPolicy(
                mode=ExecutionMode.SINGLE,
                timeout_seconds=int(stage_cfg.get("timeout_seconds", 120)),
                transport_retry=RetryPolicy(
                    max_attempts=transport_attempts,
                    retryable_errors=[
                        "TRANSPORT",
                        "TIMEOUT",
                        "RATE_LIMIT",
                        "TEMPORARY_PROVIDER_ERROR",
                    ],
                ),
                validation_retry=RetryPolicy(
                    max_attempts=validation_attempts,
                    retryable_errors=[
                        "VALIDATION",
                        "JSON_INVALID",
                        "SCHEMA_INVALID",
                    ],
                ),
                step_retry=RetryPolicy(max_attempts=1),
                retry_budget=RetryBudget(
                    max_provider_calls_per_step=provider_budget,
                    max_step_attempts=1,
                    max_validation_cycles_per_step_attempt=validation_attempts,
                    max_transport_attempts_per_model_call=transport_attempts,
                ),
                failure_policy=FailurePolicy.PARTIAL,
                model_policy={
                    "provider": stage_cfg.get("provider"),
                    "model": stage_cfg.get("model"),
                    "base_url": stage_cfg.get("base_url"),
                    "api_key_env": stage_cfg.get("api_key_env"),
                    "temperature": stage_cfg.get("temperature", 0),
                    "max_tokens": int(stage_cfg.get("max_tokens", 4096)),
                },
            )

            prompt_path = self.root / "quality_knowledge" / "prompts" / f"{stage}.md"
            prompt_hash = (
                _sha256_bytes(prompt_path.read_bytes())
                if prompt_path.exists()
                else None
            )
            runtime_agent_id = (
                f"quality_issue.{selected_agent.lower()}.{stage}"
            )
            definition = AgentDefinition(
                agent_id=runtime_agent_id,
                label=f"Quality Issue {stage}",
                enabled=bool(stage_cfg.get("enabled", True)),
                provider=str(stage_cfg.get("provider") or "openai_compatible"),
                model=str(stage_cfg.get("model") or ""),
                prompt_ref=str(prompt_path),
                output_schema={
                    "occurrence": "OccurrenceAnalysisDTO",
                    "escape": "EscapeAnalysisDTO",
                    "recurrence": "RecurrenceRiskDTO",
                    "capability_gap": "CapabilityGapDTO[]",
                }[stage],
                defaults={
                    "temperature": stage_cfg.get("temperature", 0),
                    "max_tokens": int(stage_cfg.get("max_tokens", 4096)),
                    "timeout_seconds": int(stage_cfg.get("timeout_seconds", 120)),
                },
                metadata={
                    "legacy_stage": stage,
                    "legacy_agent_id": selected_agent,
                    "model_config_path": str(config_path),
                    "prompt_hash": prompt_hash,
                    "output_schema_version": "1.0.0",
                },
            )
            definitions[stage] = definition

            depends_on = [previous_stage] if previous_stage else []
            steps.append(
                StepDefinition(
                    step_id=stage,
                    agent_id=runtime_agent_id,
                    depends_on=depends_on,
                    execution_policy=execution_policy,
                    required_for_completion=True,
                )
            )
            previous_stage = stage

        config_hash = _sha256_bytes(config_path.read_bytes())
        workflow = WorkflowDefinition(
            workflow_id="legacy_quality_issue_analysis_v2",
            version=config_hash[:12],
            steps=steps,
            failure_policy=FailurePolicy.PARTIAL,
            metadata={
                "legacy_service": "KnowledgeIssueAnalysisService",
                "legacy_agent_id": selected_agent,
                "model_config_path": str(config_path),
                "model_config_hash": config_hash,
            },
        )
        workflow_policy = ExecutionPolicy(
            mode=ExecutionMode.SEQUENTIAL,
            failure_policy=FailurePolicy.PARTIAL,
            retry_budget=RetryBudget(
                max_provider_calls_per_step=max(
                    step.execution_policy.retry_budget.max_provider_calls_per_step
                    for step in steps
                    if step.execution_policy is not None
                ),
            ),
        )
        return definitions, workflow, workflow_policy


class LegacyQualityIssueStageHandlerAdapter:
    """Reuse current StageAnalyzer business schemas without leaking Runtime internals."""

    def __init__(
        self,
        root: str | Path,
        *,
        client: Any = None,
        agent_id: str = "DEFAULT",
    ):
        from quality_knowledge.analyzers import (
            CapabilityGapAnalyzer,
            EscapeAnalyzer,
            OccurrenceAnalyzer,
            RecurrenceAnalyzer,
        )

        selected = "" if str(agent_id).upper() == "DEFAULT" else str(agent_id)
        self.analyzers = {
            "occurrence": OccurrenceAnalyzer(root, client, agent_id=selected),
            "escape": EscapeAnalyzer(root, client, agent_id=selected),
            "recurrence": RecurrenceAnalyzer(root, client, agent_id=selected),
            "capability_gap": CapabilityGapAnalyzer(
                root,
                client,
                agent_id=selected,
            ),
        }

    def handler(self, stage: str) -> Callable[[Any, dict[str, Any]], Any]:
        analyzer = self.analyzers[stage]

        def execute(business_input: Any, context: dict[str, Any]) -> Any:
            source = dict(business_input or {})
            issue = source.get("issue", source)
            payload: dict[str, Any] = {
                "issue": issue,
                "analysis_profile": source.get("analysis_profile", {}),
                "human_confirmations": source.get("human_confirmations", []),
            }
            dependencies = dict(context.get("dependencies") or {})
            if stage in ("recurrence", "capability_gap"):
                payload["occurrence_analysis"] = dependencies.get(
                    "occurrence", {}
                )
                payload["escape_analysis"] = dependencies.get("escape", {})
            if stage == "capability_gap":
                payload["recurrence_analysis"] = dependencies.get(
                    "recurrence", {}
                )

            result, _model, _debug = analyzer.analyze(payload)
            if isinstance(result, list):
                return [
                    item.model_dump(mode="json")
                    if hasattr(item, "model_dump")
                    else item
                    for item in result
                ]
            if hasattr(result, "model_dump"):
                return result.model_dump(mode="json")
            return result

        return execute


class LegacyProgressMapper:
    @staticmethod
    def map(snapshot: TaskSnapshot) -> dict[str, Any]:
        stable_status = {
            RuntimeStatus.COMPLETED: "COMPLETED",
            RuntimeStatus.PARTIAL: "PARTIAL_FAILED",
            RuntimeStatus.FAILED: "FAILED",
        }.get(snapshot.status)

        can_resume = snapshot.status == RuntimeStatus.PARTIAL or (
            snapshot.status == RuntimeStatus.WAITING
        )
        return {
            "task_id": snapshot.task_id,
            "runtime_status": snapshot.status.value,
            "legacy_status": stable_status,
            "progress_status": snapshot.status.value,
            "current_run_id": snapshot.current_run_id,
            "total_steps": snapshot.progress.total_steps,
            "completed_steps": snapshot.progress.completed_steps,
            "failed_steps": snapshot.progress.failed_steps,
            "running_steps": snapshot.progress.running_steps,
            "pending_steps": snapshot.progress.pending_steps,
            "cancel_requested": snapshot.cancel_requested,
            "can_resume": can_resume,
            "execution_state_source": "RUNTIME",
        }


class LegacyProjector:
    """Project Runtime truth into a replayable Legacy compatibility read model."""

    def __init__(
        self,
        store: SqliteTaskStore,
        *,
        fail_hook: Callable[[ProjectionOutboxEvent], None] | None = None,
    ):
        self.store = store
        self.fail_hook = fail_hook

    @staticmethod
    def _legacy_status(status: RuntimeStatus) -> str:
        return {
            RuntimeStatus.COMPLETED: "COMPLETED",
            RuntimeStatus.PARTIAL: "PARTIAL_FAILED",
            RuntimeStatus.FAILED: "FAILED",
            RuntimeStatus.CANCELLED: "CANCELLED",
            RuntimeStatus.RUNNING: "RUNNING",
            RuntimeStatus.QUEUED: "PENDING",
            RuntimeStatus.WAITING: "WAITING",
        }[status]

    def ensure_run_binding(self, run_id: str) -> LegacyProjectionBinding:
        existing = self.store.get_legacy_binding_by_run(run_id)
        if existing is not None:
            return existing

        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"runtime run not found: {run_id}")
        task = self.store.get_task(run.task_id)
        if task is None:
            raise KeyError(f"runtime task not found: {run.task_id}")

        now = _now()
        analysis_set_id = _legacy_analysis_set_id(run_id)
        binding = LegacyProjectionBinding(
            binding_id=f"legacy-run-{run_id}",
            task_id=task.task_id,
            run_id=run_id,
            legacy_analysis_set_id=analysis_set_id,
            projection_status="PENDING",
            created_at=now,
            updated_at=now,
        )
        self.store.save_legacy_projection_binding(binding)
        self.store.ensure_legacy_analysis_set(
            legacy_analysis_set_id=analysis_set_id,
            task_id=task.task_id,
            run_id=run_id,
            knowledge_id=task.metadata.get("knowledge_id")
            or task.metadata.get("business_id"),
            issue_version_id=task.metadata.get("issue_version_id"),
            status=self._legacy_status(run.status),
            metadata={
                "runtime_status": run.status.value,
                "execution_state_source": "RUNTIME",
            },
        )
        return binding

    def _bind_step(
        self,
        *,
        binding: LegacyProjectionBinding,
        step_run_id: str,
        stage: str,
        commit_id: str | None,
    ) -> None:
        now = _now()
        self.store.save_legacy_projection_binding(
            LegacyProjectionBinding(
                binding_id=f"legacy-step-{step_run_id}",
                task_id=binding.task_id,
                run_id=binding.run_id,
                legacy_analysis_set_id=binding.legacy_analysis_set_id,
                step_run_id=step_run_id,
                legacy_stage=stage,
                last_applied_commit_id=commit_id,
                projection_status="APPLIED",
                created_at=now,
                updated_at=now,
            )
        )

    def project_event(self, event: ProjectionOutboxEvent) -> None:
        try:
            if self.fail_hook is not None:
                self.fail_hook(event)
            binding = self.ensure_run_binding(event.run_id)
            if event.step_run_id is None:
                self.store.mark_projection_event_applied(event.event_id)
                return

            step = self.store.get_step_run(event.step_run_id)
            if step is None:
                raise KeyError(
                    f"runtime step not found: {event.step_run_id}"
                )
            self.store.upsert_legacy_stage_projection(
                legacy_analysis_set_id=binding.legacy_analysis_set_id,
                legacy_stage=step.step_id,
                step_run_id=step.step_run_id,
                commit_id=event.commit_id,
                status=self._legacy_status(step.status),
                result_data=event.payload.get("result_data"),
                evidence=list(event.payload.get("evidence") or []),
            )
            self._bind_step(
                binding=binding,
                step_run_id=step.step_run_id,
                stage=step.step_id,
                commit_id=event.commit_id,
            )
            self.store.mark_projection_event_applied(event.event_id)
        except Exception as exc:
            self.store.mark_projection_event_pending(
                event.event_id,
                str(exc),
            )
            raise

    def replay_pending(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, int]:
        applied = 0
        failed = 0
        for event in self.store.list_projection_events(
            task_id=task_id,
            run_id=run_id,
            status="PENDING",
        ):
            try:
                self.project_event(event)
                applied += 1
            except Exception:
                failed += 1
        return {"applied": applied, "failed": failed}

    def sync_run(self, run_id: str) -> dict[str, Any]:
        binding = self.ensure_run_binding(run_id)
        run = self.store.get_run(run_id)
        task = self.store.get_task(binding.task_id)
        if run is None or task is None:
            raise KeyError(run_id)

        # A resumed Run may reuse a previously committed execution_key and
        # therefore create no new outbox event. Re-project it into the new
        # Analysis Set without re-executing the model.
        for step in self.store.list_step_runs(run_id):
            current = {
                item["legacy_stage"]: item
                for item in self.store.list_legacy_stage_projections(
                    binding.legacy_analysis_set_id
                )
            }
            if step.step_id in current:
                continue
            execution_key = step.metadata.get("execution_key")
            committed = (
                self.store.get_committed_execution(execution_key)
                if execution_key
                else None
            )
            if committed is None:
                continue
            self.store.upsert_legacy_stage_projection(
                legacy_analysis_set_id=binding.legacy_analysis_set_id,
                legacy_stage=step.step_id,
                step_run_id=step.step_run_id,
                commit_id=committed.commit_id,
                status="COMPLETED",
                result_data=committed.result_data,
                evidence=[
                    item.model_dump(mode="json")
                    for item in committed.evidence
                ],
            )
            self._bind_step(
                binding=binding,
                step_run_id=step.step_run_id,
                stage=step.step_id,
                commit_id=committed.commit_id,
            )

        result = self.store.ensure_legacy_analysis_set(
            legacy_analysis_set_id=binding.legacy_analysis_set_id,
            task_id=task.task_id,
            run_id=run_id,
            knowledge_id=task.metadata.get("knowledge_id")
            or task.metadata.get("business_id"),
            issue_version_id=task.metadata.get("issue_version_id"),
            status=self._legacy_status(run.status),
            metadata={
                "runtime_status": run.status.value,
                "execution_state_source": "RUNTIME",
                "projection_pending": bool(
                    self.store.list_projection_events(
                        task_id=task.task_id,
                        run_id=run_id,
                        status="PENDING",
                    )
                ),
            },
        )
        binding.projection_status = (
            "PENDING"
            if result["metadata"]["projection_pending"]
            else "APPLIED"
        )
        binding.updated_at = _now()
        self.store.save_legacy_projection_binding(binding)
        return result


class LegacyQualityIssueRuntimeAdapter:
    """Low-intrusion bridge from current Quality Issue analysis to Runtime."""

    def __init__(
        self,
        runtime: LightweightExecutionEngine,
        store: SqliteTaskStore,
        root: str | Path,
        *,
        agent_id: str = "DEFAULT",
        stage_handlers: dict[str, Callable[[Any, dict[str, Any]], Any]] | None = None,
        legacy_repository: Any = None,
        legacy_reader: Callable[[str], dict[str, Any] | None] | None = None,
        projector: LegacyProjector | None = None,
    ):
        self.runtime = runtime
        self.store = store
        self.root = Path(root)
        self.agent_id = agent_id
        self.legacy_repository = legacy_repository
        self.legacy_reader = legacy_reader
        self.projector = projector or LegacyProjector(store)

        definitions, workflow, workflow_policy = QualityIssueModelYamlAdapter(
            self.root,
            agent_id=agent_id,
        ).build()
        self.agent_definitions = definitions
        self.workflow = workflow
        self.workflow_policy = workflow_policy

        if stage_handlers is None:
            handler_adapter = LegacyQualityIssueStageHandlerAdapter(
                self.root,
                agent_id=agent_id,
            )
            stage_handlers = {
                stage: handler_adapter.handler(stage)
                for stage in QUALITY_ISSUE_STAGES
            }

        for stage, definition in definitions.items():
            self.runtime.register_agent(
                definition.agent_id,
                stage_handlers[stage],
                definition,
            )
        self.runtime.register_workflow(workflow)

    def execute_issue(
        self,
        knowledge_id: str,
        *,
        issue_input: dict[str, Any] | None = None,
        issue_version_id: str | None = None,
        request_id: str | None = None,
        analysis_profile: dict[str, Any] | None = None,
        human_confirmations: list[dict[str, Any]] | None = None,
    ) -> WorkflowResult:
        if issue_input is None:
            if self.legacy_repository is None:
                raise ValueError(
                    "issue_input or legacy_repository is required"
                )
            issue_input = self.legacy_repository.get_analysis_context(
                knowledge_id
            )
            if not issue_input:
                raise KeyError(knowledge_id)

        issue_version_id = (
            issue_version_id
            or issue_input.get("issue_version_id")
        )
        payload = {
            "issue": issue_input,
            "analysis_profile": analysis_profile or {},
            "human_confirmations": human_confirmations or [],
        }
        request = WorkflowRequest(
            request_id=request_id or f"quality-issue:{knowledge_id}",
            workflow_id=self.workflow.workflow_id,
            input=payload,
            execution_policy=self.workflow_policy,
            metadata={
                "business_domain": "CASE_AND_SCENARIO",
                "business_id": knowledge_id,
                "knowledge_id": knowledge_id,
                "issue_version_id": issue_version_id,
                "legacy_projection": "QUALITY_ISSUE",
            },
        )
        result = self.runtime.execute(request)
        self.projector.ensure_run_binding(result.run_id)
        self.projector.replay_pending(
            task_id=result.task_id,
            run_id=result.run_id,
        )
        self.projector.sync_run(result.run_id)
        return result

    def resume(self, task_id: str) -> TaskSnapshot:
        self.runtime.resume(task_id)
        snapshot = self.runtime.get_task(task_id)
        if snapshot.current_run_id:
            self.projector.ensure_run_binding(snapshot.current_run_id)
            self.projector.replay_pending(
                task_id=task_id,
                run_id=snapshot.current_run_id,
            )
            self.projector.sync_run(snapshot.current_run_id)
        return self.runtime.get_task(task_id)

    def get_progress(self, task_id: str) -> dict[str, Any]:
        return LegacyProgressMapper.map(self.runtime.get_task(task_id))

    def get_projected_analysis_set(
        self,
        run_id: str,
    ) -> dict[str, Any]:
        binding = self.projector.ensure_run_binding(run_id)
        analysis_set = self.store.get_legacy_analysis_set(
            binding.legacy_analysis_set_id
        )
        if analysis_set is None:
            raise KeyError(binding.legacy_analysis_set_id)
        return {
            **analysis_set,
            "stages": self.store.list_legacy_stage_projections(
                binding.legacy_analysis_set_id
            ),
            "runtime_task_id": binding.task_id,
            "can_resume": self.runtime.get_task(
                binding.task_id
            ).status == RuntimeStatus.PARTIAL,
            "execution_state_source": "RUNTIME",
        }

    def read_analysis_set(
        self,
        legacy_analysis_set_id: str,
    ) -> dict[str, Any] | None:
        projected = self.store.get_legacy_analysis_set(
            legacy_analysis_set_id
        )
        if projected is not None:
            task = self.store.get_task(projected["task_id"])
            return {
                **projected,
                "stages": self.store.list_legacy_stage_projections(
                    legacy_analysis_set_id
                ),
                "runtime_task_id": projected["task_id"],
                "can_resume": bool(
                    task and task.status == RuntimeStatus.PARTIAL
                ),
                "execution_state_source": "RUNTIME",
            }

        if self.legacy_reader is None:
            return None
        historical = self.legacy_reader(legacy_analysis_set_id)
        if historical is None:
            return None
        return {
            **historical,
            "runtime_task_id": None,
            "can_resume": False,
            "execution_state_source": "LEGACY_HISTORY",
        }
