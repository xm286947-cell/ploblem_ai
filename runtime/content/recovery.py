from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from runtime.content.completeness import CompletenessGateEvaluator
from runtime.content.coverage import CoverageCalculator
from runtime.content.merger import ListResultMerger, MergeCoordinator, ResultMerger
from runtime.content.partials import PartialResultCommitter
from runtime.content.planner import ContentPlanner
from runtime.contracts import (
    AgentDefinition,
    CommittedPartialResult,
    CompletenessGateResult,
    ContentChunk,
    ContentPlan,
    Coverage,
    CoverageUnit,
    CoverageUniverse,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    LongContentPolicy,
    MergeContext,
    MergeResult,
    PartialResultCandidate,
    RetryBudget,
    RuntimeErrorInfo,
    RuntimeStatus,
    SourceBundle,
    StepDefinition,
    WorkflowDefinition,
    WorkflowRequest,
    WorkflowResult,
)
from runtime.providers import OpenAICompatibleProviderAdapter
from runtime.store import SqliteTaskStore


ChunkPayloadBuilder = Callable[
    [SourceBundle, ContentPlan, ContentChunk, dict[str, Any]],
    Any,
]
PartialCandidateBuilder = Callable[
    [ContentChunk, Any, str, str],
    PartialResultCandidate,
]
FinalValidator = Callable[[Any], bool]
BusinessGate = Callable[
    [MergeResult | None, list[Coverage], SourceBundle, ContentPlan],
    bool | None,
]


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _step_id(index: int, chunk: ContentChunk) -> str:
    return f"lc-{index + 1:04d}-{_stable_hash(chunk.chunk_id)[:10]}"


def _merge_key(task_id: str, plan_id: str) -> str:
    return "long-content-merge-" + _stable_hash(
        {"task_id": task_id, "plan_id": plan_id}
    )


class LongContentRecoveryOutcome(BaseModel):
    task_id: str
    run_id: str | None
    status: RuntimeStatus
    plan_id: str
    committed_partials: list[CommittedPartialResult] = Field(default_factory=list)
    coverages: list[Coverage] = Field(default_factory=list)
    merge: MergeResult | None = None
    gate: CompletenessGateResult
    business_consumable: bool = False
    provider_calls: int = 0
    runtime_status: RuntimeStatus
    runtime_error: RuntimeErrorInfo | None = None


class LongContentRecoveryExecutor:
    """Runtime-owned long-content chunk/retry/partial/merge orchestration.

    Business/domain code must project its input into SourceBundle/LogicalUnit/
    AtomicGroup before invoking this executor. Runtime never guesses business
    splitting semantics.

    One workflow step represents one ContentChunk and one successful handler
    invocation performs at most one provider request. Provider truncation is
    surfaced as the standard Runtime OUTPUT_TRUNCATED validation error; Runtime
    retry/budget logic retries only the affected chunk. Previously committed
    chunk steps are reused on resume and are never re-requested.

    The executor materializes successful chunk execution commits into durable
    CommittedPartialResult objects and runs Runtime merge/coverage/completeness
    mechanics. A business merger, final validator, and business gate can be
    supplied without moving provider/retry ownership out of Runtime.
    """

    def __init__(
        self,
        runtime,
        store: SqliteTaskStore,
        *,
        agent_id: str | None = None,
        provider_handler: Callable[[Any, dict[str, Any]], Any] | None = None,
        chunk_payload_builder: ChunkPayloadBuilder | None = None,
        partial_candidate_builder: PartialCandidateBuilder | None = None,
        merger: ResultMerger | None = None,
        final_validator: FinalValidator | None = None,
        business_gate: BusinessGate | None = None,
        step_execution_policy: ExecutionPolicy | None = None,
    ) -> None:
        self.runtime = runtime
        self.store = store
        self.agent_id = agent_id
        self.provider_handler = provider_handler
        self.chunk_payload_builder = (
            chunk_payload_builder or self._default_chunk_payload
        )
        self.partial_candidate_builder = (
            partial_candidate_builder or self._default_partial_candidate
        )
        self.merger = merger or ListResultMerger()
        self.final_validator = final_validator
        self.business_gate = business_gate
        self.step_execution_policy = step_execution_policy
        self.planner = ContentPlanner()
        self.partial_committer = PartialResultCommitter()
        self.coverage_calculator = CoverageCalculator()
        self.gate_evaluator = CompletenessGateEvaluator()

    def bind_agent(
        self,
        *,
        agent_id: str,
        provider_handler: Callable[[Any, dict[str, Any]], Any],
        definition: AgentDefinition | None = None,
        execution_policy: ExecutionPolicy | None = None,
    ) -> AgentDefinition:
        self.agent_id = agent_id
        self.provider_handler = provider_handler
        self.step_execution_policy = execution_policy
        resolved_definition = definition or AgentDefinition(
            agent_id=agent_id,
            label="Runtime Long Content Chunk Provider",
            metadata={
                "runtime_long_content": True,
                "provider_call": True,
            },
        )
        self.runtime.register_agent(
            agent_id,
            self._handler,
            resolved_definition,
        )
        return resolved_definition

    def bind_configured_agent(self, config_path: str | Path):
        """Bind an Agent Config while preserving Runtime provider ownership."""
        loader = getattr(self.runtime, "config_loader", None)
        load_agent = getattr(self.runtime, "load_agent", None)
        if loader is None or not callable(load_agent):
            raise TypeError(
                "bind_configured_agent requires ConfiguredAgentRuntime"
            )

        resolved = loader.load(config_path)
        if resolved.provider.type != "openai_compatible":
            raise ValueError(
                "LONG_CONTENT_PROVIDER_TYPE_UNSUPPORTED:"
                + resolved.provider.type
            )
        self.agent_id = resolved.definition.agent_id
        self.step_execution_policy = resolved.execution_policy
        self.provider_handler = OpenAICompatibleProviderAdapter(
            system_prompt=loader.read_prompt_text(resolved),
            output_schema=loader.get_output_schema(resolved),
            timeout_seconds=resolved.execution_policy.timeout_seconds,
            response_shape=resolved.definition.metadata.get(
                "provider_response_shape"
            ),
        )
        return load_agent(config_path, self._handler)

    @staticmethod
    def _default_chunk_payload(
        bundle: SourceBundle,
        plan: ContentPlan,
        chunk: ContentChunk,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        units = {
            item.unit_id: item
            for item in bundle.logical_units
        }
        ordered_ids = list(chunk.unit_ids) + list(chunk.overlap_unit_ids)
        source_ids = list(
            dict.fromkeys(
                units[unit_id].source_id
                for unit_id in ordered_ids
                if unit_id in units
            )
        )
        sources = {
            item.source.source_id: item
            for item in bundle.sources
        }
        return {
            "plan_id": plan.plan_id,
            "chunk": chunk.model_dump(mode="json"),
            "logical_units": [
                units[unit_id].model_dump(mode="json")
                for unit_id in ordered_ids
                if unit_id in units
            ],
            "sources": [
                sources[source_id].model_dump(mode="json")
                for source_id in source_ids
                if source_id in sources
            ],
            "shared_context": dict(chunk.shared_context),
        }

    @staticmethod
    def _default_partial_candidate(
        chunk: ContentChunk,
        data: Any,
        execution_key: str,
        step_id: str,
    ) -> PartialResultCandidate:
        return PartialResultCandidate(
            chunk_id=chunk.chunk_id,
            execution_key=execution_key,
            unit_ids=list(chunk.unit_ids),
            data=data,
            partition_key=chunk.partition_key,
            schema_valid=True,
            complete_object=True,
            finish_reason="stop",
            evidence_ids=[],
            metadata={
                "step_id": step_id,
                "bundle_id": chunk.bundle_id,
                "overlap_unit_ids": list(chunk.overlap_unit_ids),
            },
        )

    def _handler(
        self,
        business_input: Any,
        context: dict[str, Any],
    ) -> Any:
        if self.provider_handler is None:
            raise RuntimeError("LONG_CONTENT_PROVIDER_NOT_BOUND")
        payload = dict(business_input or {})
        bundle = SourceBundle.model_validate(payload["bundle"])
        plan = ContentPlan.model_validate(payload["plan"])
        runtime_context = context.get("runtime") or {}
        step_id = str(runtime_context.get("step_id") or "")
        chunk_id = str(
            (payload.get("step_chunk_map") or {}).get(step_id) or ""
        )
        chunk = next(
            (item for item in plan.chunks if item.chunk_id == chunk_id),
            None,
        )
        if chunk is None:
            raise RuntimeError(
                f"LONG_CONTENT_CHUNK_NOT_FOUND:{step_id}:{chunk_id}"
            )

        provider_context = {
            **context,
            "long_content": {
                "plan_id": plan.plan_id,
                "chunk_id": chunk.chunk_id,
                "partition_key": chunk.partition_key,
                "unit_ids": list(chunk.unit_ids),
                "overlap_unit_ids": list(chunk.overlap_unit_ids),
            },
        }
        provider_payload = self.chunk_payload_builder(
            bundle,
            plan,
            chunk,
            provider_context,
        )
        return self.provider_handler(
            provider_payload,
            provider_context,
        )

    def _resolved_step_policy(
        self,
        *,
        chunk_count: int,
        max_provider_calls_per_task: int | None,
    ) -> ExecutionPolicy:
        base = self.step_execution_policy or ExecutionPolicy()
        per_step = max(
            1,
            int(base.retry_budget.max_provider_calls_per_step),
        )
        task_limit = (
            int(max_provider_calls_per_task)
            if max_provider_calls_per_task is not None
            else (
                int(base.retry_budget.max_provider_calls_per_task)
                if base.retry_budget.max_provider_calls_per_task is not None
                else max(1, chunk_count * per_step)
            )
        )
        budget = base.retry_budget.model_copy(
            update={
                "max_provider_calls_per_task": task_limit,
            }
        )
        return base.model_copy(
            update={
                "mode": ExecutionMode.SINGLE,
                "retry_budget": budget,
                "failure_policy": FailurePolicy.PARTIAL,
            }
        )

    def _workflow(
        self,
        plan: ContentPlan,
        *,
        step_policy: ExecutionPolicy,
    ) -> tuple[WorkflowDefinition, dict[str, str]]:
        if not self.agent_id:
            raise RuntimeError("LONG_CONTENT_AGENT_NOT_BOUND")
        step_chunk_map: dict[str, str] = {}
        steps: list[StepDefinition] = []
        previous: str | None = None
        for index, chunk in enumerate(plan.chunks):
            step_id = _step_id(index, chunk)
            step_chunk_map[step_id] = chunk.chunk_id
            steps.append(
                StepDefinition(
                    step_id=step_id,
                    agent_id=self.agent_id,
                    depends_on=[previous] if previous else [],
                    execution_policy=step_policy,
                    required_for_completion=True,
                    partition_policy="ISOLATED",
                )
            )
            previous = step_id

        workflow_id = "long-content-" + _stable_hash(
            {
                "agent_id": self.agent_id,
                "plan_id": plan.plan_id,
                "steps": step_chunk_map,
            }
        )[:24]
        workflow = WorkflowDefinition(
            workflow_id=workflow_id,
            version=plan.plan_id,
            steps=steps,
            failure_policy=FailurePolicy.PARTIAL,
            metadata={
                "runtime_long_content": True,
                "plan_id": plan.plan_id,
                "strategy_ref": plan.strategy_ref,
            },
        )
        return workflow, step_chunk_map

    def _materialize_partials(
        self,
        result: WorkflowResult,
        plan: ContentPlan,
        step_chunk_map: dict[str, str],
    ) -> list[CommittedPartialResult]:
        chunks = {item.chunk_id: item for item in plan.chunks}
        step_runs = {
            item.step_id: item
            for item in self.store.list_step_runs(result.run_id)
        }
        for step_id, summary in result.step_results.items():
            if summary.status != RuntimeStatus.COMPLETED:
                continue
            chunk_id = step_chunk_map.get(step_id)
            chunk = chunks.get(str(chunk_id or ""))
            step_run = step_runs.get(step_id)
            if chunk is None or step_run is None:
                continue
            execution_key = str(
                step_run.metadata.get("execution_key") or ""
            )
            if not execution_key:
                continue
            candidate = self.partial_candidate_builder(
                chunk,
                summary.data,
                execution_key,
                step_id,
            )
            committed = self.partial_committer.commit(candidate)
            self.store.commit_partial_result(
                task_id=result.task_id,
                run_id=result.run_id,
                step_run_id=step_run.step_run_id,
                partial=committed,
            )

        order = {
            chunk.chunk_id: index
            for index, chunk in enumerate(plan.chunks)
        }
        return sorted(
            self.store.list_partial_results(result.task_id),
            key=lambda item: order.get(item.chunk_id, len(order)),
        )

    @staticmethod
    def _unit_partition(
        bundle: SourceBundle,
        unit_id: str,
    ) -> str | None:
        unit = next(
            item for item in bundle.logical_units if item.unit_id == unit_id
        )
        if unit.partition_key is not None:
            return unit.partition_key
        source = next(
            (
                item
                for item in bundle.sources
                if item.source.source_id == unit.source_id
            ),
            None,
        )
        if source is not None and source.partition_key is not None:
            return source.partition_key
        return bundle.default_partition_key

    def _coverages(
        self,
        bundle: SourceBundle,
        partials: list[CommittedPartialResult],
    ) -> list[Coverage]:
        processed = {
            unit_id
            for partial in partials
            for unit_id in partial.unit_ids
        }
        source_map = {
            item.source.source_id: item.source
            for item in bundle.sources
        }
        groups: dict[tuple[str, str | None], list[Any]] = {}
        for unit in bundle.logical_units:
            key = (
                unit.source_id,
                self._unit_partition(bundle, unit.unit_id),
            )
            groups.setdefault(key, []).append(unit)

        coverages: list[Coverage] = []
        for (source_id, partition_key), units in groups.items():
            source = source_map[source_id]
            universe = CoverageUniverse(
                source=source,
                coverage_type="ITEM",
                unit_targets=[
                    CoverageUnit(
                        unit_id=unit.unit_id,
                        locator=dict(unit.locator),
                    )
                    for unit in units
                ],
                universe_fingerprint=_stable_hash(
                    {
                        "source": source.fingerprint,
                        "partition_key": partition_key,
                        "unit_ids": [unit.unit_id for unit in units],
                    }
                ),
                partition_key=partition_key,
            )
            coverages.append(
                self.coverage_calculator.calculate(
                    universe,
                    processed_unit_ids=processed,
                )
            )
        return coverages

    def _finalize(
        self,
        *,
        result: WorkflowResult,
        bundle: SourceBundle,
        plan: ContentPlan,
        step_chunk_map: dict[str, str],
    ) -> LongContentRecoveryOutcome:
        partials = self._materialize_partials(
            result,
            plan,
            step_chunk_map,
        )
        coverages = self._coverages(bundle, partials)
        present_chunks = {item.chunk_id for item in partials}
        all_chunks_present = all(
            chunk.chunk_id in present_chunks
            for chunk in plan.chunks
        )

        merge: MergeResult | None = None
        if all_chunks_present:
            merge = MergeCoordinator(self.store).merge_and_commit(
                self.merger,
                partials,
                MergeContext(
                    merge_key=_merge_key(result.task_id, plan.plan_id),
                    expected_partial_ids=[
                        item.partial_id for item in partials
                    ],
                    partition_key=(
                        bundle.default_partition_key
                        if len({
                            item.partition_key
                            for item in partials
                        }) <= 1
                        else None
                    ),
                    partition_policy=(
                        "ISOLATED"
                        if len({
                            item.partition_key
                            for item in partials
                        }) <= 1
                        else "CROSS_PARTITION"
                    ),
                    metadata={
                        "runtime_long_content": True,
                        "plan_id": plan.plan_id,
                        "strategy_ref": plan.strategy_ref,
                    },
                ),
            )

        schema_valid = True
        if merge is not None and self.final_validator is not None:
            try:
                schema_valid = bool(self.final_validator(merge.data))
            except Exception:
                schema_valid = False

        business_gate_passed: bool | None = None
        if self.business_gate is not None:
            business_gate_passed = bool(
                self.business_gate(
                    merge,
                    coverages,
                    bundle,
                    plan,
                )
            )

        gate = self.gate_evaluator.evaluate(
            coverage=coverages,
            schema_valid=schema_valid,
            merge_result=merge,
            merge_complete=(merge is not None and merge.complete),
            evidence_integrity=True,
            business_gate_passed=business_gate_passed,
            gate_ref="RUNTIME_LONG_CONTENT_RECOVERY",
            gate_version="1",
        )

        status = result.status
        if status == RuntimeStatus.COMPLETED and not gate.passed:
            status = RuntimeStatus.PARTIAL

        return LongContentRecoveryOutcome(
            task_id=result.task_id,
            run_id=result.run_id,
            status=status,
            plan_id=plan.plan_id,
            committed_partials=partials,
            coverages=coverages,
            merge=merge,
            gate=gate,
            business_consumable=bool(
                status == RuntimeStatus.COMPLETED
                and gate.passed
            ),
            provider_calls=self.store.count_task_provider_calls(
                result.task_id
            ),
            runtime_status=result.status,
            runtime_error=result.error,
        )

    def execute(
        self,
        bundle: SourceBundle,
        *,
        request_id: str,
        policy: LongContentPolicy | dict[str, Any] | None = None,
        strategy_ref: str | None = None,
        max_provider_calls_per_task: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LongContentRecoveryOutcome:
        if not self.agent_id or self.provider_handler is None:
            raise RuntimeError("LONG_CONTENT_AGENT_NOT_BOUND")

        plan = self.planner.plan(
            bundle,
            policy,
            strategy_ref=strategy_ref,
        )
        step_policy = self._resolved_step_policy(
            chunk_count=len(plan.chunks),
            max_provider_calls_per_task=max_provider_calls_per_task,
        )
        workflow, step_chunk_map = self._workflow(
            plan,
            step_policy=step_policy,
        )
        self.runtime.register_workflow(workflow)
        workflow_policy = ExecutionPolicy(
            mode=ExecutionMode.SEQUENTIAL,
            failure_policy=FailurePolicy.PARTIAL,
            retry_budget=RetryBudget(
                max_provider_calls_per_step=(
                    step_policy.retry_budget.max_provider_calls_per_step
                ),
                max_provider_calls_per_task=(
                    step_policy.retry_budget.max_provider_calls_per_task
                ),
            ),
            long_content_policy={
                "enabled": True,
                "strategy_ref": strategy_ref,
                "plan_id": plan.plan_id,
            },
        )
        result = self.runtime.execute(
            WorkflowRequest(
                request_id=request_id,
                workflow_id=workflow.workflow_id,
                input={
                    "bundle": bundle.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                    "step_chunk_map": step_chunk_map,
                },
                execution_policy=workflow_policy,
                metadata={
                    **(metadata or {}),
                    "runtime_long_content": True,
                    "plan_id": plan.plan_id,
                    "strategy_ref": strategy_ref,
                    "partition_key": (
                        bundle.default_partition_key
                        or "__multi__"
                    ),
                },
            )
        )
        return self._finalize(
            result=result,
            bundle=bundle,
            plan=plan,
            step_chunk_map=step_chunk_map,
        )

    def resume(self, task_id: str) -> LongContentRecoveryOutcome:
        request = self.store.load_request(task_id)
        if not isinstance(request, WorkflowRequest):
            raise TypeError(
                "long-content recovery task must be a WorkflowRequest"
            )
        payload = dict(request.input or {})
        bundle = SourceBundle.model_validate(payload["bundle"])
        plan = ContentPlan.model_validate(payload["plan"])
        step_chunk_map = {
            str(key): str(value)
            for key, value in (
                payload.get("step_chunk_map") or {}
            ).items()
        }
        self.runtime.resume(task_id)
        snapshot = self.runtime.get_task(task_id)
        result = snapshot.result
        if not isinstance(result, WorkflowResult):
            raise RuntimeError(
                "LONG_CONTENT_RESUME_RESULT_MISSING"
            )
        return self._finalize(
            result=result,
            bundle=bundle,
            plan=plan,
            step_chunk_map=step_chunk_map,
        )


__all__ = [
    "AdaptiveLongContentRecoveryExecutor",
    "AdaptiveLongContentRecoveryOutcome",
    "AdaptiveLongContentRecoveryPolicy",
    "BusinessGate",
    "ChunkPayloadBuilder",
    "FinalValidator",
    "LongContentRecoveryExecutor",
    "LongContentRecoveryOutcome",
    "PartialCandidateBuilder",
]


class AdaptiveLongContentRecoveryPolicy(BaseModel):
    """Deterministic Runtime policy for truncation-triggered re-planning."""

    max_replans: int = Field(default=4, ge=0, le=16)
    max_provider_calls: int = Field(default=12, ge=1)
    shrink_factor: float = Field(default=0.5, gt=0.0, lt=1.0)
    min_units_per_chunk: int = Field(default=1, ge=1)
    min_payload_chars: int = Field(default=256, ge=1)


class AdaptiveLongContentRecoveryOutcome(BaseModel):
    recovery_request_id: str
    status: RuntimeStatus
    task_ids: list[str] = Field(default_factory=list)
    plan_ids: list[str] = Field(default_factory=list)
    committed_partials: list[CommittedPartialResult] = Field(default_factory=list)
    coverages: list[Coverage] = Field(default_factory=list)
    merge: MergeResult | None = None
    gate: CompletenessGateResult
    business_consumable: bool = False
    provider_calls: int = 0
    replans: int = 0
    truncated_task_ids: list[str] = Field(default_factory=list)
    terminal_error: RuntimeErrorInfo | None = None


AdaptiveFaultInjector = Callable[[str, dict[str, Any]], None]


class AdaptiveLongContentRecoveryExecutor:
    """Adaptive Runtime long-content recovery over persisted generation tasks.

    The first generation uses the caller's LongContentPolicy. If a Runtime
    chunk ends with OUTPUT_TRUNCATED, already committed chunks remain durable.
    Only uncovered LogicalUnits are re-planned with smaller chunk limits.

    Each generation uses a deterministic request id, so re-running the same
    recovery request reconstructs prior progress from Runtime Task/Partial
    state instead of repeating completed provider calls.
    """

    def __init__(
        self,
        executor: LongContentRecoveryExecutor,
        *,
        recovery_policy: AdaptiveLongContentRecoveryPolicy | None = None,
        fault_injector: AdaptiveFaultInjector | None = None,
    ) -> None:
        self.executor = executor
        self.runtime = executor.runtime
        self.store = executor.store
        self.recovery_policy = (
            recovery_policy or AdaptiveLongContentRecoveryPolicy()
        )
        self.fault_injector = fault_injector

    def _inject(self, point: str, context: dict[str, Any]) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point, context)

    @staticmethod
    def _generation_request_id(
        recovery_request_id: str,
        generation: int,
    ) -> str:
        return (
            f"{recovery_request_id}:long-content:g{generation}:"
            + _stable_hash(
                {
                    "recovery_request_id": recovery_request_id,
                    "generation": generation,
                }
            )[:12]
        )

    @staticmethod
    def _pending_bundle(
        bundle: SourceBundle,
        covered_unit_ids: set[str],
    ) -> SourceBundle:
        pending_units = [
            item
            for item in bundle.logical_units
            if item.unit_id not in covered_unit_ids
        ]
        pending_ids = {item.unit_id for item in pending_units}
        pending_groups = []
        for group in bundle.atomic_groups:
            remaining = [
                unit_id
                for unit_id in group.unit_ids
                if unit_id in pending_ids
            ]
            if not remaining:
                continue
            # A committed KEEP_TOGETHER group must have been committed as one
            # chunk. Seeing only part of it pending would violate the business
            # atomicity contract and must never be silently reinterpreted.
            if (
                str(group.policy.value) == "KEEP_TOGETHER"
                and len(remaining) != len(group.unit_ids)
            ):
                raise RuntimeError(
                    "LONG_CONTENT_ATOMIC_GROUP_PARTIAL_STATE:"
                    + group.group_id
                )
            pending_groups.append(
                group.model_copy(update={"unit_ids": remaining})
            )

        return bundle.model_copy(
            update={
                "logical_units": pending_units,
                "atomic_groups": pending_groups,
                "metadata": {
                    **bundle.metadata,
                    "adaptive_pending_units": sorted(pending_ids),
                },
            }
        )

    @staticmethod
    def _atomic_floors(bundle: SourceBundle) -> tuple[int, int]:
        unit_map = {item.unit_id: item for item in bundle.logical_units}

        def unit_size(unit_id: str) -> int:
            unit = unit_map[unit_id]
            if "estimated_payload_chars" in unit.metadata:
                return max(
                    0,
                    int(unit.metadata["estimated_payload_chars"]),
                )
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

        min_units = 1
        min_chars = 1
        for group in bundle.atomic_groups:
            if str(group.policy.value) != "KEEP_TOGETHER":
                continue
            min_units = max(min_units, len(group.unit_ids))
            min_chars = max(
                min_chars,
                sum(
                    unit_size(unit_id)
                    for unit_id in group.unit_ids
                    if unit_id in unit_map
                ),
            )
        return min_units, min_chars

    def _generation_policy(
        self,
        initial: LongContentPolicy,
        *,
        generation: int,
        bundle: SourceBundle,
    ) -> LongContentPolicy:
        factor = self.recovery_policy.shrink_factor ** generation
        atomic_units, atomic_chars = self._atomic_floors(bundle)
        max_units = max(
            self.recovery_policy.min_units_per_chunk,
            atomic_units,
            int(initial.max_units_per_chunk * factor),
        )
        max_chars = max(
            self.recovery_policy.min_payload_chars,
            atomic_chars,
            int(initial.max_payload_chars * factor),
        )
        return initial.model_copy(
            update={
                "max_units_per_chunk": max_units,
                "max_payload_chars": max_chars,
            }
        )

    @staticmethod
    def _dedupe_partials(
        items: list[CommittedPartialResult],
    ) -> list[CommittedPartialResult]:
        by_id: dict[str, CommittedPartialResult] = {}
        for item in items:
            by_id[item.partial_id] = item
        return list(by_id.values())

    def _existing_generation_outcome(
        self,
        request_id: str,
    ) -> LongContentRecoveryOutcome | None:
        task = self.store.get_task_by_request_id(request_id)
        if task is None:
            return None
        return self.executor.outcome(task.task_id)

    def _finalize(
        self,
        *,
        recovery_request_id: str,
        bundle: SourceBundle,
        task_ids: list[str],
        plan_ids: list[str],
        partials: list[CommittedPartialResult],
        provider_calls: int,
        replans: int,
        truncated_task_ids: list[str],
        terminal_error: RuntimeErrorInfo | None,
    ) -> AdaptiveLongContentRecoveryOutcome:
        partials = self._dedupe_partials(partials)
        coverages = self.executor._coverages(bundle, partials)
        coverage_complete = bool(coverages) and all(
            item.complete for item in coverages
        )

        merge: MergeResult | None = None
        if coverage_complete:
            partitions = {item.partition_key for item in partials}
            merge = MergeCoordinator(self.store).merge_and_commit(
                self.executor.merger,
                partials,
                MergeContext(
                    merge_key=(
                        "adaptive-long-content-merge-"
                        + _stable_hash(
                            {
                                "recovery_request_id": recovery_request_id,
                                "bundle_id": bundle.bundle_id,
                            }
                        )
                    ),
                    expected_partial_ids=[
                        item.partial_id for item in partials
                    ],
                    partition_key=(
                        next(iter(partitions))
                        if len(partitions) == 1
                        else None
                    ),
                    partition_policy=(
                        "ISOLATED"
                        if len(partitions) <= 1
                        else "CROSS_PARTITION"
                    ),
                    metadata={
                        "runtime_long_content": True,
                        "adaptive_recovery": True,
                        "recovery_request_id": recovery_request_id,
                        "replans": replans,
                    },
                ),
            )

        schema_valid = True
        if (
            merge is not None
            and self.executor.final_validator is not None
        ):
            try:
                schema_valid = bool(
                    self.executor.final_validator(merge.data)
                )
            except Exception:
                schema_valid = False

        business_gate_passed: bool | None = None
        if (
            merge is not None
            and self.executor.business_gate is not None
        ):
            business_gate_passed = self.executor.business_gate(
                merge,
                coverages,
                bundle,
                ContentPlan(
                    plan_id=(
                        plan_ids[-1]
                        if plan_ids
                        else "adaptive-empty"
                    ),
                    bundle_id=bundle.bundle_id,
                    chunks=[],
                    metadata={
                        "adaptive_recovery": True,
                        "generation_plan_ids": list(plan_ids),
                    },
                ),
            )

        gate = self.executor.gate_evaluator.evaluate(
            coverage=coverages,
            schema_valid=schema_valid,
            merge_result=merge,
            merge_complete=(
                merge is not None
                and merge.complete
                and coverage_complete
            ),
            evidence_integrity=True,
            business_gate_passed=business_gate_passed,
            gate_ref="RUNTIME_ADAPTIVE_LONG_CONTENT_RECOVERY",
            gate_version="1",
        )

        status = (
            RuntimeStatus.COMPLETED
            if gate.passed and terminal_error is None
            else RuntimeStatus.PARTIAL
        )
        return AdaptiveLongContentRecoveryOutcome(
            recovery_request_id=recovery_request_id,
            status=status,
            task_ids=list(dict.fromkeys(task_ids)),
            plan_ids=list(plan_ids),
            committed_partials=partials,
            coverages=coverages,
            merge=merge,
            gate=gate,
            business_consumable=bool(
                status == RuntimeStatus.COMPLETED
                and gate.passed
            ),
            provider_calls=provider_calls,
            replans=replans,
            truncated_task_ids=list(
                dict.fromkeys(truncated_task_ids)
            ),
            terminal_error=terminal_error,
        )

    def execute(
        self,
        bundle: SourceBundle,
        *,
        recovery_request_id: str,
        initial_policy: LongContentPolicy | dict[str, Any] | None = None,
        strategy_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AdaptiveLongContentRecoveryOutcome:
        if not bundle.logical_units:
            raise ValueError("LONG_CONTENT_SOURCE_BUNDLE_EMPTY")

        initial = (
            initial_policy
            if isinstance(initial_policy, LongContentPolicy)
            else LongContentPolicy.model_validate(
                initial_policy or {}
            )
        )
        committed: list[CommittedPartialResult] = []
        covered: set[str] = set()
        task_ids: list[str] = []
        plan_ids: list[str] = []
        truncated_task_ids: list[str] = []
        provider_calls = 0
        terminal_error: RuntimeErrorInfo | None = None
        replans = 0

        for generation in range(
            self.recovery_policy.max_replans + 1
        ):
            pending = self._pending_bundle(bundle, covered)
            if not pending.logical_units:
                break

            remaining_budget = (
                self.recovery_policy.max_provider_calls
                - provider_calls
            )
            if remaining_budget <= 0:
                terminal_error = RuntimeErrorInfo(
                    code="RETRY_BUDGET_EXHAUSTED",
                    category="EXECUTION",
                    message=(
                        "adaptive long-content provider-call budget exhausted"
                    ),
                    retryable=True,
                    details={
                        "max_provider_calls": (
                            self.recovery_policy.max_provider_calls
                        ),
                        "provider_calls": provider_calls,
                    },
                )
                break

            generation_policy = self._generation_policy(
                initial,
                generation=generation,
                bundle=pending,
            )
            generation_request_id = self._generation_request_id(
                recovery_request_id,
                generation,
            )

            outcome = self._existing_generation_outcome(
                generation_request_id
            )
            if outcome is None:
                outcome = self.executor.execute(
                    pending,
                    request_id=generation_request_id,
                    policy=generation_policy,
                    strategy_ref=strategy_ref,
                    max_provider_calls_per_task=remaining_budget,
                    metadata={
                        **(metadata or {}),
                        "adaptive_recovery": True,
                        "recovery_request_id": recovery_request_id,
                        "generation": generation,
                    },
                )

            task_ids.append(outcome.task_id)
            plan_ids.append(outcome.plan_id)
            provider_calls += outcome.provider_calls
            committed.extend(outcome.committed_partials)
            covered.update(
                unit_id
                for partial in outcome.committed_partials
                for unit_id in partial.unit_ids
            )

            self._inject(
                "after_generation",
                {
                    "generation": generation,
                    "task_id": outcome.task_id,
                    "plan_id": outcome.plan_id,
                    "covered_unit_ids": sorted(covered),
                    "provider_calls": provider_calls,
                },
            )

            if outcome.status == RuntimeStatus.COMPLETED:
                continue

            error = outcome.runtime_error
            if (
                error is not None
                and error.code == "OUTPUT_TRUNCATED"
            ):
                truncated_task_ids.append(outcome.task_id)
                if generation >= self.recovery_policy.max_replans:
                    terminal_error = error
                    break

                next_policy = self._generation_policy(
                    initial,
                    generation=generation + 1,
                    bundle=self._pending_bundle(bundle, covered),
                )
                if (
                    next_policy.max_units_per_chunk
                    >= generation_policy.max_units_per_chunk
                    and next_policy.max_payload_chars
                    >= generation_policy.max_payload_chars
                ):
                    terminal_error = RuntimeErrorInfo(
                        code="LONG_CONTENT_ATOMIC_UNIT_STILL_TRUNCATED",
                        category="VALIDATION",
                        message=(
                            "truncated content cannot be split further "
                            "without violating atomic-group limits"
                        ),
                        retryable=False,
                        details={
                            "generation": generation,
                            "max_units_per_chunk": (
                                generation_policy.max_units_per_chunk
                            ),
                            "max_payload_chars": (
                                generation_policy.max_payload_chars
                            ),
                        },
                    )
                    break

                replans += 1
                continue

            terminal_error = error or RuntimeErrorInfo(
                code="LONG_CONTENT_GENERATION_INCOMPLETE",
                category="EXECUTION",
                message="long-content generation did not complete",
                retryable=True,
                details={"generation": generation},
            )
            break

        return self._finalize(
            recovery_request_id=recovery_request_id,
            bundle=bundle,
            task_ids=task_ids,
            plan_ids=plan_ids,
            partials=committed,
            provider_calls=provider_calls,
            replans=replans,
            truncated_task_ids=truncated_task_ids,
            terminal_error=terminal_error,
        )
