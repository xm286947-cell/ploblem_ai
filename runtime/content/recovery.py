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
    "BusinessGate",
    "ChunkPayloadBuilder",
    "FinalValidator",
    "LongContentRecoveryExecutor",
    "LongContentRecoveryOutcome",
    "PartialCandidateBuilder",
]
