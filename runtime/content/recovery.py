from __future__ import annotations

from collections import deque
import hashlib
import json
from typing import Any, Callable

from pydantic import BaseModel, Field

from runtime.contracts import (
    AgentRequest,
    AgentResult,
    AtomicGroupPolicy,
    CommittedPartialResult,
    CompletenessGateResult,
    ContentChunk,
    Coverage,
    CoverageUnit,
    CoverageUniverse,
    ExecutionPolicy,
    LongContentPolicy,
    MergeContext,
    MergeResult,
    PartialResultCandidate,
    RetryBudget,
    RuntimeErrorInfo,
    RuntimeStatus,
    SourceBundle,
    SourceRef,
)
from runtime.content.completeness import CompletenessGateEvaluator
from runtime.content.coverage import CoverageCalculator
from runtime.content.merger import ListResultMerger, MergeCoordinator, ResultMerger
from runtime.content.partials import PartialResultCommitter
from runtime.content.planner import ContentPlanner
from runtime.reliability.errors import RuntimeStepError
from runtime.contracts import ErrorCategory


ChunkInputBuilder = Callable[[ContentChunk, SourceBundle], Any]
PartialProjector = Callable[
    [ContentChunk, Any],
    list[PartialResultCandidate | dict[str, Any]],
]
BusinessGate = Callable[
    [MergeResult, Coverage, list[CommittedPartialResult]],
    bool | CompletenessGateResult,
]


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{_stable_hash(value)}"


class LongContentRecoveryOutcome(BaseModel):
    operation_id: str
    status: RuntimeStatus
    provider_calls: int = 0
    child_task_ids: list[str] = Field(default_factory=list)
    committed_partials: list[CommittedPartialResult] = Field(default_factory=list)
    coverage: Coverage | None = None
    merge: MergeResult | None = None
    gate: CompletenessGateResult | None = None
    business_consumable: bool = False
    pending_unit_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: RuntimeErrorInfo | None = None


class LongContentRecoveryCoordinator:
    """Runtime-owned long-content truncation recovery.

    Business/domain code supplies:
    - SourceBundle / AtomicGroup declarations
    - chunk input projection
    - result -> independently-valid partial projection
    - optional business-specific merge and completeness semantics

    Runtime owns:
    - deterministic chunk planning
    - provider execution via Runtime agent invocations
    - OUTPUT_TRUNCATED recovery by atomically-safe chunk splitting
    - global provider-call budget
    - partial persistence / idempotency
    - resume from committed partials
    - merge execution mechanics
    - coverage/completeness gating
    """

    def __init__(
        self,
        runtime,
        store,
        *,
        agent_id: str,
        chunk_input_builder: ChunkInputBuilder,
        partial_projector: PartialProjector,
        merger: ResultMerger | None = None,
        business_gate: BusinessGate | None = None,
    ) -> None:
        self.runtime = runtime
        self.store = store
        self.agent_id = agent_id
        self.chunk_input_builder = chunk_input_builder
        self.partial_projector = partial_projector
        self.merger = merger or ListResultMerger()
        self.business_gate = business_gate
        self.planner = ContentPlanner()
        self.partial_committer = PartialResultCommitter()
        self.coverage_calculator = CoverageCalculator()
        self.gate_evaluator = CompletenessGateEvaluator()

    @staticmethod
    def _bundle_fingerprint(bundle: SourceBundle) -> str:
        return _stable_hash(bundle.model_dump(mode="json"))

    @staticmethod
    def _unit_size(unit) -> int:
        if "estimated_payload_chars" in unit.metadata:
            try:
                return max(0, int(unit.metadata["estimated_payload_chars"]))
            except (TypeError, ValueError):
                pass
        if unit.inline_payload is None:
            return 0
        return len(
            json.dumps(
                unit.inline_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            )
        )

    def _split_chunk(
        self,
        chunk: ContentChunk,
        bundle: SourceBundle,
    ) -> list[ContentChunk]:
        """Split only at AtomicGroup-safe boundaries."""
        unit_by_id = {item.unit_id: item for item in bundle.logical_units}
        order = {
            item.unit_id: index
            for index, item in enumerate(bundle.logical_units)
        }

        keep_group_by_unit: dict[str, list[str]] = {}
        for group in bundle.atomic_groups:
            if group.policy != AtomicGroupPolicy.KEEP_TOGETHER:
                continue
            ordered = sorted(
                [
                    unit_id
                    for unit_id in group.unit_ids
                    if unit_id in unit_by_id
                ],
                key=lambda unit_id: order[unit_id],
            )
            for unit_id in ordered:
                keep_group_by_unit[unit_id] = ordered

        components: list[list[str]] = []
        seen: set[str] = set()
        for unit_id in chunk.unit_ids:
            if unit_id in seen:
                continue
            component = keep_group_by_unit.get(unit_id, [unit_id])
            component = [
                item for item in component if item in chunk.unit_ids
            ]
            components.append(component)
            seen.update(component)

        if len(components) < 2:
            raise RuntimeStepError(
                "truncated chunk cannot be split without breaking an AtomicGroup",
                code="LONG_CONTENT_ATOMIC_UNIT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=False,
                details={
                    "chunk_id": chunk.chunk_id,
                    "unit_ids": list(chunk.unit_ids),
                },
            )

        weights = [
            sum(self._unit_size(unit_by_id[unit_id]) for unit_id in component)
            for component in components
        ]
        total = sum(weights)
        best_index = 1
        best_distance = None
        running = 0
        for index in range(1, len(components)):
            running += weights[index - 1]
            distance = abs(total - 2 * running)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_index = index

        groups = [
            components[:best_index],
            components[best_index:],
        ]
        children: list[ContentChunk] = []
        depth = int(chunk.metadata.get("recovery_depth", 0)) + 1
        for side, component_group in enumerate(groups, 1):
            unit_ids = [
                unit_id
                for component in component_group
                for unit_id in component
            ]
            estimated = sum(
                self._unit_size(unit_by_id[unit_id])
                for unit_id in unit_ids
            )
            child_id = _stable_id(
                "recovery-chunk",
                {
                    "parent": chunk.chunk_id,
                    "side": side,
                    "unit_ids": unit_ids,
                    "depth": depth,
                },
            )
            children.append(
                ContentChunk(
                    chunk_id=child_id,
                    bundle_id=chunk.bundle_id,
                    partition_key=chunk.partition_key,
                    unit_ids=unit_ids,
                    overlap_unit_ids=[],
                    shared_context=dict(chunk.shared_context),
                    estimated_payload_chars=estimated,
                    metadata={
                        **chunk.metadata,
                        "recovery_depth": depth,
                        "recovery_parent_chunk_id": chunk.chunk_id,
                        "overlap_dropped_for_truncation_recovery": bool(
                            chunk.overlap_unit_ids
                        ),
                    },
                )
            )
        return children

    def _base_execution_policy(self) -> ExecutionPolicy:
        getter = getattr(self.runtime, "get_agent_config", None)
        if callable(getter):
            try:
                return getter(self.agent_id).execution_policy
            except (KeyError, AttributeError):
                pass
        return ExecutionPolicy()

    @staticmethod
    def _budget_limit(
        recovery_budget: RetryBudget,
    ) -> int:
        return int(
            recovery_budget.max_provider_calls_per_task
            or recovery_budget.max_provider_calls_per_step
        )

    def _child_policy(
        self,
        *,
        base: ExecutionPolicy,
        remaining_provider_calls: int,
        strategy_ref: str | None,
    ) -> ExecutionPolicy:
        step_limit = min(
            base.retry_budget.max_provider_calls_per_step,
            remaining_provider_calls,
        )
        budget = base.retry_budget.model_copy(
            update={
                "max_provider_calls_per_step": max(1, step_limit),
                "max_provider_calls_per_task": max(1, remaining_provider_calls),
            }
        )
        long_policy = {
            **base.long_content_policy,
            "enabled": True,
            "strategy_ref": strategy_ref,
            "recovery_coordinator": "runtime.content.LongContentRecoveryCoordinator",
        }
        return base.model_copy(
            update={
                "retry_budget": budget,
                "long_content_policy": long_policy,
            }
        )

    def _existing_or_invoke(
        self,
        request: AgentRequest,
    ) -> AgentResult:
        existing = self.store.get_task_by_request_id(request.request_id)
        if existing is not None:
            snapshot = self.store.get_task_snapshot(existing.task_id)
            if isinstance(snapshot.result, AgentResult):
                return snapshot.result
            raise RuntimeStepError(
                "existing long-content child task has no reusable result",
                code="LONG_CONTENT_CHILD_RESULT_MISSING",
                category=ErrorCategory.EXECUTION,
                retryable=False,
                details={
                    "request_id": request.request_id,
                    "task_id": existing.task_id,
                    "status": existing.status.value,
                },
            )
        return self.runtime.invoke(request)

    @staticmethod
    def _child_step_run_id(store, result: AgentResult) -> str:
        step_runs = store.list_step_runs(result.run_id)
        if not step_runs:
            return f"recovery-step:{result.run_id}"
        return step_runs[0].step_run_id

    def _coverage(
        self,
        *,
        bundle: SourceBundle,
        source: SourceRef,
        partials: list[CommittedPartialResult],
        partition_key: str | None,
    ) -> Coverage:
        unit_ids = [item.unit_id for item in bundle.logical_units]
        return self.coverage_calculator.calculate(
            CoverageUniverse(
                source=source,
                coverage_type="ITEM",
                unit_targets=[
                    CoverageUnit(
                        unit_id=item.unit_id,
                        locator=dict(item.locator),
                    )
                    for item in bundle.logical_units
                ],
                universe_fingerprint=_stable_hash(
                    {
                        "bundle_id": bundle.bundle_id,
                        "source_fingerprint": source.fingerprint,
                        "unit_ids": unit_ids,
                        "partition_key": partition_key,
                    }
                ),
                partition_key=partition_key,
                metadata={
                    "bundle_id": bundle.bundle_id,
                },
            ),
            processed_unit_ids=[
                unit_id
                for partial in partials
                for unit_id in partial.unit_ids
            ],
        )

    @staticmethod
    def _source(
        bundle: SourceBundle,
        explicit: SourceRef | None,
    ) -> SourceRef:
        if explicit is not None:
            return explicit
        if len(bundle.sources) != 1:
            raise RuntimeStepError(
                "coverage_source is required for multi-source long-content recovery",
                code="LONG_CONTENT_COVERAGE_SOURCE_REQUIRED",
                category=ErrorCategory.CONFIG,
                retryable=False,
                details={"source_count": len(bundle.sources)},
            )
        return bundle.sources[0].source

    @staticmethod
    def _validate_resume_bundle(
        partials: list[CommittedPartialResult],
        bundle_fingerprint: str,
    ) -> None:
        for partial in partials:
            previous = partial.metadata.get("bundle_fingerprint")
            if previous and previous != bundle_fingerprint:
                raise RuntimeStepError(
                    "long-content source bundle changed after partial commit",
                    code="SOURCE_IDENTITY_CHANGED",
                    category=ErrorCategory.CONFIG,
                    retryable=False,
                    details={
                        "expected_bundle_fingerprint": previous,
                        "actual_bundle_fingerprint": bundle_fingerprint,
                        "partial_id": partial.partial_id,
                    },
                )

    def execute(
        self,
        *,
        operation_id: str,
        bundle: SourceBundle,
        policy: LongContentPolicy,
        recovery_budget: RetryBudget,
        strategy_ref: str | None = None,
        coverage_source: SourceRef | None = None,
        partition_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LongContentRecoveryOutcome:
        strategy_ref = strategy_ref or ""
        metadata = dict(metadata or {})
        source = self._source(bundle, coverage_source)
        bundle_fingerprint = self._bundle_fingerprint(bundle)
        plan = self.planner.plan(
            bundle,
            policy,
            strategy_ref=strategy_ref or None,
        )

        queue: deque[ContentChunk] = deque(
            [
                chunk
                for chunk in plan.chunks
                if partition_key is None
                or chunk.partition_key == partition_key
            ]
        )
        committed = self.store.list_partial_results(
            operation_id,
            partition_key=partition_key,
        )
        self._validate_resume_bundle(committed, bundle_fingerprint)

        provider_calls = 0
        child_task_ids: list[str] = []
        seen_child_tasks: set[str] = set()
        warnings: list[str] = []
        global_limit = self._budget_limit(recovery_budget)
        base_policy = self._base_execution_policy()

        while queue:
            processed_units = {
                unit_id
                for partial in committed
                for unit_id in partial.unit_ids
            }
            chunk = queue.popleft()
            if set(chunk.unit_ids).issubset(processed_units):
                continue

            request_id = _stable_id(
                "long-content-child",
                {
                    "operation_id": operation_id,
                    "chunk_id": chunk.chunk_id,
                    "agent_id": self.agent_id,
                    "bundle_fingerprint": bundle_fingerprint,
                },
            )

            existing = self.store.get_task_by_request_id(request_id)
            if existing is None and provider_calls >= global_limit:
                coverage = self._coverage(
                    bundle=bundle,
                    source=source,
                    partials=committed,
                    partition_key=partition_key,
                )
                error = RuntimeErrorInfo(
                    code="RETRY_BUDGET_EXHAUSTED",
                    category=ErrorCategory.EXECUTION,
                    message="long-content recovery provider-call budget exhausted",
                    retryable=False,
                    details={
                        "operation_id": operation_id,
                        "provider_calls": provider_calls,
                        "provider_call_limit": global_limit,
                    },
                )
                return LongContentRecoveryOutcome(
                    operation_id=operation_id,
                    status=RuntimeStatus.PARTIAL,
                    provider_calls=provider_calls,
                    child_task_ids=child_task_ids,
                    committed_partials=committed,
                    coverage=coverage,
                    pending_unit_ids=list(coverage.pending_units),
                    warnings=warnings,
                    error=error,
                )

            remaining = max(1, global_limit - provider_calls)
            child_policy = self._child_policy(
                base=base_policy,
                remaining_provider_calls=remaining,
                strategy_ref=strategy_ref or None,
            )
            request = AgentRequest(
                request_id=request_id,
                agent_id=self.agent_id,
                input=self.chunk_input_builder(chunk, bundle),
                execution_policy=child_policy,
                metadata={
                    "business_domain": bundle.metadata.get("business_domain"),
                    "business_id": operation_id,
                    "partition_key": chunk.partition_key,
                    "long_content_operation_id": operation_id,
                    "long_content_chunk_id": chunk.chunk_id,
                    "content_strategy_ref": strategy_ref or None,
                    **metadata,
                },
            )
            result = self._existing_or_invoke(request)

            if result.task_id not in seen_child_tasks:
                provider_calls += int(result.execution.provider_calls)
                child_task_ids.append(result.task_id)
                seen_child_tasks.add(result.task_id)

            if provider_calls > global_limit:
                raise RuntimeStepError(
                    "child Runtime task exceeded long-content recovery budget",
                    code="LONG_CONTENT_BUDGET_INVARIANT_BROKEN",
                    category=ErrorCategory.EXECUTION,
                    retryable=False,
                    details={
                        "provider_calls": provider_calls,
                        "provider_call_limit": global_limit,
                        "child_task_id": result.task_id,
                    },
                )

            if result.status == RuntimeStatus.COMPLETED:
                projected = self.partial_projector(chunk, result.data)
                step_run_id = self._child_step_run_id(self.store, result)
                for raw in projected:
                    candidate = (
                        raw
                        if isinstance(raw, PartialResultCandidate)
                        else PartialResultCandidate.model_validate(raw)
                    )
                    if not candidate.execution_key.strip():
                        raise RuntimeStepError(
                            "partial projector must supply stable execution_key",
                            code="LONG_CONTENT_PARTIAL_KEY_REQUIRED",
                            category=ErrorCategory.BUSINESS,
                            retryable=False,
                        )
                    unknown_units = sorted(
                        set(candidate.unit_ids) - set(chunk.unit_ids)
                    )
                    if unknown_units:
                        raise RuntimeStepError(
                            "partial references units outside the executed chunk",
                            code="LONG_CONTENT_PARTIAL_UNIT_SCOPE_INVALID",
                            category=ErrorCategory.BUSINESS,
                            retryable=False,
                            details={
                                "chunk_id": chunk.chunk_id,
                                "unknown_unit_ids": unknown_units,
                            },
                        )
                    candidate = candidate.model_copy(
                        update={
                            "chunk_id": chunk.chunk_id,
                            "partition_key": chunk.partition_key,
                            "metadata": {
                                **candidate.metadata,
                                "bundle_fingerprint": bundle_fingerprint,
                                "long_content_operation_id": operation_id,
                                "content_strategy_ref": strategy_ref or None,
                            },
                        }
                    )
                    partial = self.partial_committer.commit(candidate)
                    self.store.commit_partial_result(
                        task_id=operation_id,
                        run_id=result.run_id,
                        step_run_id=step_run_id,
                        partial=partial,
                    )
                committed = self.store.list_partial_results(
                    operation_id,
                    partition_key=partition_key,
                )
                self._validate_resume_bundle(
                    committed,
                    bundle_fingerprint,
                )
                continue

            if (
                result.error is not None
                and result.error.code == "OUTPUT_TRUNCATED"
            ):
                children = self._split_chunk(chunk, bundle)
                warnings.append(
                    f"OUTPUT_TRUNCATED:{chunk.chunk_id}"
                )
                for child in reversed(children):
                    queue.appendleft(child)
                continue

            coverage = self._coverage(
                bundle=bundle,
                source=source,
                partials=committed,
                partition_key=partition_key,
            )
            return LongContentRecoveryOutcome(
                operation_id=operation_id,
                status=(
                    RuntimeStatus.PARTIAL
                    if result.status == RuntimeStatus.PARTIAL
                    else RuntimeStatus.FAILED
                ),
                provider_calls=provider_calls,
                child_task_ids=child_task_ids,
                committed_partials=committed,
                coverage=coverage,
                pending_unit_ids=list(coverage.pending_units),
                warnings=warnings,
                error=result.error,
            )

        committed = self.store.list_partial_results(
            operation_id,
            partition_key=partition_key,
        )
        coverage = self._coverage(
            bundle=bundle,
            source=source,
            partials=committed,
            partition_key=partition_key,
        )
        if not coverage.complete:
            return LongContentRecoveryOutcome(
                operation_id=operation_id,
                status=RuntimeStatus.PARTIAL,
                provider_calls=provider_calls,
                child_task_ids=child_task_ids,
                committed_partials=committed,
                coverage=coverage,
                pending_unit_ids=list(coverage.pending_units),
                warnings=warnings,
                error=RuntimeErrorInfo(
                    code="LONG_CONTENT_COVERAGE_INCOMPLETE",
                    category=ErrorCategory.BUSINESS,
                    message="long-content recovery finished without complete unit coverage",
                    retryable=True,
                    details={
                        "pending_unit_ids": list(coverage.pending_units),
                    },
                ),
            )

        merge_key = _stable_id(
            "long-content-merge",
            {
                "operation_id": operation_id,
                "bundle_fingerprint": bundle_fingerprint,
                "partition_key": partition_key,
                "strategy_ref": strategy_ref,
            },
        )
        merge = MergeCoordinator(self.store).merge_and_commit(
            self.merger,
            committed,
            MergeContext(
                merge_key=merge_key,
                expected_partial_ids=[
                    item.partial_id for item in committed
                ],
                partition_key=partition_key,
                partition_policy=(
                    "ISOLATED"
                    if partition_key is not None
                    else "CROSS_PARTITION"
                ),
                metadata={
                    "operation_id": operation_id,
                    "bundle_id": bundle.bundle_id,
                    "content_strategy_ref": strategy_ref or None,
                },
            ),
        )

        business_gate_passed = True
        gate: CompletenessGateResult
        if self.business_gate is not None:
            business_gate_value = self.business_gate(
                merge,
                coverage,
                committed,
            )
            if isinstance(
                business_gate_value,
                CompletenessGateResult,
            ):
                gate = business_gate_value
            else:
                business_gate_passed = bool(business_gate_value)
                gate = self.gate_evaluator.evaluate(
                    coverage=coverage,
                    schema_valid=True,
                    merge_result=merge,
                    evidence_integrity=True,
                    business_gate_passed=business_gate_passed,
                    gate_ref=strategy_ref or "LONG_CONTENT",
                    gate_version="1",
                )
        else:
            gate = self.gate_evaluator.evaluate(
                coverage=coverage,
                schema_valid=True,
                merge_result=merge,
                evidence_integrity=True,
                business_gate_passed=True,
                gate_ref=strategy_ref or "LONG_CONTENT",
                gate_version="1",
            )

        status = (
            RuntimeStatus.COMPLETED
            if gate.passed
            else RuntimeStatus.PARTIAL
        )
        return LongContentRecoveryOutcome(
            operation_id=operation_id,
            status=status,
            provider_calls=provider_calls,
            child_task_ids=child_task_ids,
            committed_partials=committed,
            coverage=coverage,
            merge=merge,
            gate=gate,
            business_consumable=bool(gate.passed),
            pending_unit_ids=list(coverage.pending_units),
            warnings=warnings,
        )


__all__ = [
    "BusinessGate",
    "ChunkInputBuilder",
    "LongContentRecoveryCoordinator",
    "LongContentRecoveryOutcome",
    "PartialProjector",
]
