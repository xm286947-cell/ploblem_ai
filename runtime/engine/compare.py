from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from runtime.contracts import (
    AgentResult,
    RuntimeStatus,
    WorkflowResult,
)
from runtime.store import SqliteTaskStore


class EngineObservation(BaseModel):
    engine: str
    status: RuntimeStatus
    completion: dict[str, Any]
    data: Any = None
    step_statuses: dict[str, RuntimeStatus] = Field(default_factory=dict)
    warning_codes: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_retryable: bool | None = None
    provider_calls: int
    checkpoint_count: int
    retry_budget_exhausted: bool
    resumed: bool
    run_count: int
    latest_run_sequence: int
    latest_resume_of_run_id_present: bool
    committed_execution_count: int


class EngineComparison(BaseModel):
    equivalent: bool
    mismatches: list[str] = Field(default_factory=list)
    left: EngineObservation
    right: EngineObservation


class EngineCompareHarness:
    """Compare contract-visible and durable execution facts.

    IDs, timestamps, duration and framework-internal graph state are
    intentionally excluded because independent engines/stores naturally
    produce different identities and timings.
    """

    @staticmethod
    def observe(
        *,
        engine_name: str,
        result: AgentResult | WorkflowResult,
        store: SqliteTaskStore,
    ) -> EngineObservation:
        step_statuses: dict[str, RuntimeStatus] = {}
        if isinstance(result, WorkflowResult):
            step_statuses = {
                step_id: item.status
                for step_id, item in result.step_results.items()
            }

        runs = store.list_runs(result.task_id)
        latest_run = runs[-1]
        committed = store.list_committed_execution_keys(result.task_id)
        return EngineObservation(
            engine=engine_name,
            status=result.status,
            completion=result.completion.model_dump(mode="json"),
            data=result.data,
            step_statuses=step_statuses,
            warning_codes=sorted(item.code for item in result.warnings),
            error_code=result.error.code if result.error else None,
            error_retryable=(
                result.error.retryable if result.error else None
            ),
            provider_calls=result.execution.provider_calls,
            checkpoint_count=result.execution.checkpoint_count,
            retry_budget_exhausted=result.execution.retry_budget_exhausted,
            resumed=result.execution.resumed,
            run_count=len(runs),
            latest_run_sequence=latest_run.run_sequence,
            latest_resume_of_run_id_present=(
                latest_run.resume_of_run_id is not None
            ),
            committed_execution_count=len(committed),
        )

    @staticmethod
    def compare(
        left: EngineObservation,
        right: EngineObservation,
    ) -> EngineComparison:
        mismatches: list[str] = []
        comparable_fields = (
            "status",
            "completion",
            "data",
            "step_statuses",
            "warning_codes",
            "error_code",
            "error_retryable",
            "provider_calls",
            "checkpoint_count",
            "retry_budget_exhausted",
            "resumed",
            "run_count",
            "latest_run_sequence",
            "latest_resume_of_run_id_present",
            "committed_execution_count",
        )
        for field_name in comparable_fields:
            if getattr(left, field_name) != getattr(right, field_name):
                mismatches.append(field_name)

        return EngineComparison(
            equivalent=not mismatches,
            mismatches=mismatches,
            left=left,
            right=right,
        )
