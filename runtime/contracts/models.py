from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskType(str, Enum):
    AGENT = "AGENT"
    WORKFLOW = "WORKFLOW"


class ExecutionMode(str, Enum):
    SINGLE = "SINGLE"
    SEQUENTIAL = "SEQUENTIAL"
    PARALLEL = "PARALLEL"


class FailurePolicy(str, Enum):
    CONTINUE = "CONTINUE"
    STOP = "STOP"
    PARTIAL = "PARTIAL"


class AttemptType(str, Enum):
    TRANSPORT = "TRANSPORT"
    VALIDATION = "VALIDATION"
    STEP = "STEP"


class ErrorCategory(str, Enum):
    CONFIG = "CONFIG"
    TRANSPORT = "TRANSPORT"
    VALIDATION = "VALIDATION"
    EXECUTION = "EXECUTION"
    BUSINESS = "BUSINESS"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class RetryPolicy(ContractModel):
    max_attempts: int = 1
    backoff_seconds: float = 0.0
    retryable_errors: list[str] = Field(default_factory=list)


class RetryBudget(ContractModel):
    max_provider_calls_per_step: int = 1
    max_provider_calls_per_task: int | None = None
    max_step_attempts: int = 1
    max_validation_cycles_per_step_attempt: int = 1
    max_transport_attempts_per_model_call: int = 1
    max_elapsed_seconds_per_step: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionPolicy(ContractModel):
    mode: ExecutionMode = ExecutionMode.SINGLE
    timeout_seconds: int | None = None
    transport_retry: RetryPolicy = Field(default_factory=RetryPolicy)
    validation_retry: RetryPolicy = Field(default_factory=RetryPolicy)
    step_retry: RetryPolicy = Field(default_factory=RetryPolicy)
    retry_budget: RetryBudget = Field(default_factory=RetryBudget)
    long_content_policy: dict[str, Any] = Field(default_factory=dict)
    checkpoint_policy: dict[str, Any] = Field(default_factory=dict)
    failure_policy: FailurePolicy = FailurePolicy.STOP
    model_policy: dict[str, Any] = Field(default_factory=dict)


class AgentDefinition(ContractModel):
    agent_id: str
    label: str | None = None
    enabled: bool = True
    provider: str | None = None
    model: str | None = None
    prompt_ref: str | None = None
    output_schema: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: str | None = None
    definition_hash: str | None = None
    content_strategy_ref: str | None = None


class StepDefinition(ContractModel):
    step_id: str
    agent_id: str
    depends_on: list[str] = Field(default_factory=list)
    condition: dict[str, Any] | None = None
    input_mapping: dict[str, Any] = Field(default_factory=dict)
    output_mapping: dict[str, Any] = Field(default_factory=dict)
    execution_policy: ExecutionPolicy | None = None
    required_for_completion: bool = True
    partition_policy: Literal["ISOLATED", "CROSS_PARTITION"] = "ISOLATED"


class WorkflowDefinition(ContractModel):
    workflow_id: str
    version: str
    steps: list[StepDefinition]
    input_mapping: dict[str, Any] = Field(default_factory=dict)
    output_mapping: dict[str, Any] = Field(default_factory=dict)
    failure_policy: FailurePolicy = FailurePolicy.STOP
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRequest(ContractModel):
    request_id: str
    agent_id: str
    input: Any
    context: dict[str, Any] = Field(default_factory=dict)
    output_schema: str | None = None
    execution_policy: ExecutionPolicy | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRequest(ContractModel):
    request_id: str
    workflow_id: str
    input: Any
    context: dict[str, Any] = Field(default_factory=dict)
    workflow: WorkflowDefinition | None = None
    execution_policy: ExecutionPolicy | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


_RUNTIME_CREDENTIAL_FIELDS = frozenset(
    {
        "provider_api_key",
        "provider_access_token",
        "provider_bearer_token",
        "provider_credential",
        "runtime_api_key",
        "runtime_access_token",
        "runtime_bearer_token",
        "runtime_credential",
        "runtime_credentials",
        "runtime_secret",
    }
)


def strip_runtime_credentials(value: Any) -> Any:
    """Return persistence-safe auxiliary Runtime data without altering input.

    The reserved fields identify credentials injected by Runtime/provider
    configuration. They are intentionally distinct from ordinary business
    fields such as ``password``, ``api_key``, ``token``, and ``secret``.
    """
    if isinstance(value, dict):
        return {
            key: strip_runtime_credentials(item)
            for key, item in value.items()
            if str(key).lower() not in _RUNTIME_CREDENTIAL_FIELDS
        }
    if isinstance(value, list):
        return [strip_runtime_credentials(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_runtime_credentials(item) for item in value)
    return value


def canonical_request_payload(
    request: AgentRequest | WorkflowRequest,
) -> dict[str, Any]:
    """Build the durable request projection used for task persistence.

    Business input remains verbatim. Runtime credentials are resolved again at
    execution time from the configured provider and are never task data.
    """
    payload = request.model_dump(mode="json")
    payload["context"] = strip_runtime_credentials(payload["context"])
    payload["metadata"] = strip_runtime_credentials(payload["metadata"])
    if isinstance(request, WorkflowRequest) and payload["workflow"] is not None:
        payload["workflow"]["metadata"] = strip_runtime_credentials(
            payload["workflow"]["metadata"]
        )
    return payload


class SourceRef(ContractModel):
    source_id: str
    source_type: str
    revision: str | None = None
    content_hash: str | None = None
    fingerprint: str
    uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Range(ContractModel):
    start: int | float
    end: int | float
    locator: dict[str, Any] | None = None


class Coverage(ContractModel):
    source: SourceRef
    universe_fingerprint: str
    coverage_type: Literal["RANGE", "PAGE", "SECTION", "ITEM"]
    required_units: list[str] = Field(default_factory=list)
    processed_units: list[str] = Field(default_factory=list)
    failed_units: list[str] = Field(default_factory=list)
    pending_units: list[str] = Field(default_factory=list)
    processed_ranges: list[Range] = Field(default_factory=list)
    failed_ranges: list[Range] = Field(default_factory=list)
    pending_ranges: list[Range] = Field(default_factory=list)
    coverage_ratio: float = 0.0
    complete: bool = False
    partition_key: str | None = None


class EvidenceLocator(ContractModel):
    type: Literal["FIELD", "PAGE", "SECTION", "ROW", "RANGE", "EVENT", "BUSINESS_RECORD"]
    value: dict[str, Any] = Field(default_factory=dict)


class EvidenceReference(ContractModel):
    evidence_id: str
    source: SourceRef
    locator: EvidenceLocator
    excerpt: str | None = None
    confidence: float | None = None
    derived_from: list[str] = Field(default_factory=list)
    partition_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeErrorInfo(ContractModel):
    code: str
    category: ErrorCategory
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class RuntimeWarning(ContractModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class CompletenessGateResult(ContractModel):
    source_coverage_complete: bool | None = None
    schema_valid: bool = True
    merge_complete: bool = True
    evidence_integrity: bool = True
    business_gate_passed: bool | None = None
    passed: bool = True
    reasons: list[str] = Field(default_factory=list)
    gate_ref: str | None = None
    gate_version: str | None = None


class CompletionSummary(ContractModel):
    gate: CompletenessGateResult
    business_consumable: bool
    incomplete_reasons: list[str] = Field(default_factory=list)


class ExecutionSummary(ContractModel):
    provider: str | None = None
    model: str | None = None
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    transport_attempts: int = 0
    validation_attempts: int = 0
    step_attempts: int = 0
    token_usage: dict[str, Any] = Field(default_factory=dict)
    checkpoint_count: int = 0
    resumed: bool = False
    trace_id: str
    provider_calls: int = 0
    retry_budget_limit: int | None = None
    retry_budget_consumed: int = 0
    retry_budget_exhausted: bool = False
    execution_snapshot_id: str | None = None
    execution_definition_fingerprint: str | None = None


class StepResultSummary(ContractModel):
    step_id: str
    agent_id: str
    status: RuntimeStatus
    data: Any = None
    error: RuntimeErrorInfo | None = None


class AgentResult(ContractModel):
    task_id: str
    run_id: str
    request_id: str
    agent_id: str
    status: RuntimeStatus
    data: Any = None
    coverage: Coverage | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    execution: ExecutionSummary
    completion: CompletionSummary
    warnings: list[RuntimeWarning] = Field(default_factory=list)
    error: RuntimeErrorInfo | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowResult(ContractModel):
    task_id: str
    run_id: str
    request_id: str
    workflow_id: str
    status: RuntimeStatus
    data: Any = None
    step_results: dict[str, StepResultSummary] = Field(default_factory=dict)
    coverage: Coverage | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    execution: ExecutionSummary
    completion: CompletionSummary
    warnings: list[RuntimeWarning] = Field(default_factory=list)
    error: RuntimeErrorInfo | None = None


class TaskRecord(ContractModel):
    task_id: str
    request_id: str
    request_fingerprint: str
    task_type: TaskType
    business_domain: str | None = None
    business_id: str | None = None
    status: RuntimeStatus
    current_run_id: str | None = None
    input_hash: str
    execution_snapshot_id: str | None = None
    execution_definition_fingerprint: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    completed_at: datetime | None = None
    cancel_requested: bool = False
    cancel_requested_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunRecord(ContractModel):
    run_id: str
    task_id: str
    workflow_id: str | None = None
    workflow_version: str | None = None
    status: RuntimeStatus
    input_hash: str
    run_sequence: int = 1
    resume_of_run_id: str | None = None
    execution_snapshot_id: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    error: RuntimeErrorInfo | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StepRunRecord(ContractModel):
    step_run_id: str
    run_id: str
    step_id: str
    agent_id: str
    status: RuntimeStatus
    attempt_count: int
    input_hash: str
    output_ref: str | None = None
    coverage: Coverage | None = None
    error: RuntimeErrorInfo | None = None
    started_at: datetime
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AttemptRecord(ContractModel):
    attempt_id: str
    step_run_id: str
    attempt_no: int
    attempt_type: AttemptType
    status: RuntimeStatus
    execution_key: str | None = None
    replayed_after_crash: bool = False
    step_attempt_no: int = 1
    validation_cycle_no: int = 0
    transport_attempt_no: int = 0
    provider_call_seq: int | None = None
    parent_attempt_id: str | None = None
    trigger_error_category: str | None = None
    model_name: str | None = None
    provider: str | None = None
    raw_response_ref: str | None = None
    parsed_result_ref: str | None = None
    error: RuntimeErrorInfo | None = None
    started_at: datetime
    completed_at: datetime | None = None
    execution_metrics: dict[str, Any] = Field(default_factory=dict)


class CheckpointRecord(ContractModel):
    checkpoint_id: str
    task_id: str
    run_id: str
    step_run_id: str
    input_hash: str
    status: RuntimeStatus
    source_id: str | None = None
    processed_range: Range | None = None
    result_ref: str | None = None
    evidence_ref: str | None = None
    execution_key: str | None = None
    commit_id: str
    checkpoint_sequence: int
    partition_key: str | None = None
    created_at: datetime


class ExecutionCommit(ContractModel):
    commit_id: str
    task_id: str
    run_id: str
    step_run: StepRunRecord
    attempt: AttemptRecord
    checkpoint: CheckpointRecord
    execution_key: str
    result_data: Any = None
    coverage: Coverage | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    status: RuntimeStatus = RuntimeStatus.COMPLETED


class CommitResult(ContractModel):
    commit_id: str
    execution_key: str
    inserted: bool


class CommittedExecution(ContractModel):
    commit_id: str
    task_id: str
    run_id: str
    step_run_id: str
    execution_key: str
    result_data: Any = None
    coverage: Coverage | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    status: RuntimeStatus
    created_at: datetime


class TaskHandle(ContractModel):
    task_id: str
    status: RuntimeStatus


class TaskProgress(ContractModel):
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    running_steps: int = 0
    pending_steps: int = 0


class TaskSnapshot(ContractModel):
    task_id: str
    request_id: str
    status: RuntimeStatus
    current_run_id: str | None
    progress: TaskProgress
    result: AgentResult | WorkflowResult | None = None
    error: RuntimeErrorInfo | None = None
    created_at: datetime
    updated_at: datetime
    cancel_requested: bool = False
    cancel_requested_at: datetime | None = None


class ExecutionDefinitionSnapshot(ContractModel):
    snapshot_id: str
    fingerprint: str
    canonical_contract_version: str = "P0.2_CONTRACT_FROZEN_V1.0"
    agent_definition: dict[str, Any] | None = None
    agent_definition_hash: str | None = None
    workflow_definition: WorkflowDefinition | None = None
    workflow_definition_hash: str | None = None
    prompt_ref: str | None = None
    prompt_hash: str | None = None
    output_schema_ref: str | None = None
    output_schema_version: str | None = None
    output_schema_hash: str | None = None
    content_strategy_ref: str | None = None
    content_strategy_version: str | None = None
    content_strategy_hash: str | None = None
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    model_policy: dict[str, Any] = Field(default_factory=dict)
    completeness_gate_ref: str | None = None
    completeness_gate_version: str | None = None
    runtime_version: str = "p0.3"
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectionOutboxEvent(ContractModel):
    event_id: str
    task_id: str
    run_id: str
    step_run_id: str | None = None
    commit_id: str
    projection_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["PENDING", "APPLIED", "FAILED"] = "PENDING"
    attempt_count: int = 0
    last_error: str | None = None
    created_at: datetime
    applied_at: datetime | None = None


class LegacyProjectionBinding(ContractModel):
    binding_id: str
    task_id: str
    run_id: str
    legacy_analysis_set_id: str
    step_run_id: str | None = None
    legacy_stage: str | None = None
    projection_version: str = "1"
    last_applied_commit_id: str | None = None
    projection_status: Literal["PENDING", "APPLIED", "FAILED"] = "PENDING"
    created_at: datetime
    updated_at: datetime


class AtomicGroupPolicy(str, Enum):
    KEEP_TOGETHER = "KEEP_TOGETHER"
    SAME_CONTEXT = "SAME_CONTEXT"


class ContentStrategyDefinition(ContractModel):
    strategy_id: str
    version: str
    projector_ref: str
    planner_ref: str
    merger_ref: str | None = None
    grouping_ref: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.strategy_id}@{self.version}"


class ContentSource(ContractModel):
    source: SourceRef
    content_ref: str | None = None
    inline_content: Any = None
    partition_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicalUnit(ContractModel):
    unit_id: str
    source_id: str
    locator: dict[str, Any] = Field(default_factory=dict)
    payload_ref: str | None = None
    inline_payload: Any = None
    group_id: str | None = None
    context_refs: list[str] = Field(default_factory=list)
    partition_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AtomicGroup(ContractModel):
    group_id: str
    unit_ids: list[str]
    policy: AtomicGroupPolicy
    grouping_hint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceBundle(ContractModel):
    bundle_id: str
    sources: list[ContentSource]
    logical_units: list[LogicalUnit]
    atomic_groups: list[AtomicGroup] = Field(default_factory=list)
    shared_context: dict[str, Any] = Field(default_factory=dict)
    default_partition_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LongContentPolicy(ContractModel):
    max_units_per_chunk: int = 8
    max_payload_chars: int = 8000
    overlap_units: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContentChunk(ContractModel):
    chunk_id: str
    bundle_id: str
    partition_key: str | None = None
    unit_ids: list[str]
    overlap_unit_ids: list[str] = Field(default_factory=list)
    shared_context: dict[str, Any] = Field(default_factory=dict)
    estimated_payload_chars: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContentPlan(ContractModel):
    plan_id: str
    bundle_id: str
    strategy_ref: str | None = None
    chunks: list[ContentChunk]
    metadata: dict[str, Any] = Field(default_factory=dict)


class PartialResultCandidate(ContractModel):
    chunk_id: str
    execution_key: str
    unit_ids: list[str]
    data: Any
    partition_key: str | None = None
    schema_valid: bool
    complete_object: bool
    finish_reason: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommittedPartialResult(ContractModel):
    partial_id: str
    chunk_id: str
    execution_key: str
    unit_ids: list[str]
    data: Any
    partition_key: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    committed_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoverageUnit(ContractModel):
    unit_id: str
    locator: dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoverageUniverse(ContractModel):
    source: SourceRef
    coverage_type: Literal["RANGE", "PAGE", "SECTION", "ITEM"]
    range_targets: list[Range] = Field(default_factory=list)
    unit_targets: list[CoverageUnit] = Field(default_factory=list)
    universe_fingerprint: str
    partition_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MergeContext(ContractModel):
    merge_key: str
    expected_partial_ids: list[str]
    partition_key: str | None = None
    partition_policy: Literal["ISOLATED", "CROSS_PARTITION"] = "ISOLATED"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MergeResult(ContractModel):
    merge_key: str
    data: Any = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    derived_from_partial_ids: list[str] = Field(default_factory=list)
    complete: bool
    missing_partial_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    name
    for name, value in globals().items()
    if isinstance(value, type) and getattr(value, "__module__", None) == __name__
] + ["canonical_request_payload", "strip_runtime_credentials"]
