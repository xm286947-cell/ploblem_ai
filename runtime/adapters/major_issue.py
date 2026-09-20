from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field

from runtime.content import (
    CompletenessGateEvaluator,
    CoverageCalculator,
    EvidenceRegistry,
    ListResultMerger,
    MergeCoordinator,
    PartialResultCommitter,
)
from runtime.content.errors import InvalidPartialResultError
from runtime.contracts import (
    AgentDefinition,
    AgentRequest,
    CommittedPartialResult,
    CompletenessGateResult,
    Coverage,
    CoverageUnit,
    CoverageUniverse,
    ErrorCategory,
    EvidenceLocator,
    EvidenceReference,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    MergeContext,
    MergeResult,
    PartialResultCandidate,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    SourceRef,
    StepDefinition,
    WorkflowDefinition,
    WorkflowRequest,
)
from runtime.engine import LightweightExecutionEngine
from runtime.reliability import RuntimeStepError
from runtime.store import SqliteTaskStore


MajorIssueProvider = Callable[
    [dict[str, Any], list[dict[str, Any]], dict[str, Any]],
    list[Any],
]
RepeatCaseAgent = Callable[[Any, dict[str, Any]], Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MajorIssueObjectSpec(BaseModel):
    object_id: str
    unit_id: str
    locator: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MajorIssueObjectCandidate(BaseModel):
    object_id: str
    data: Any
    schema_valid: bool = True
    complete_object: bool = True
    finish_reason: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MajorIssueD01Outcome(BaseModel):
    task_id: str
    run_id: str | None
    status: RuntimeStatus
    partition_key: str
    committed_objects: list[dict[str, Any]]
    coverage: Coverage
    merge: MergeResult | None = None
    gate: CompletenessGateResult | None = None
    evidence_integrity: bool = True
    evidence_lineage_ids: list[str] = Field(default_factory=list)
    business_consumable: bool = False
    provider_calls: int = 0


class MajorIssueD01RuntimeAdapter:
    """D01 long-output adapter over the generic Runtime.

    A provider response may contain several independently complete structured
    objects followed by a truncated/invalid object. Valid objects are committed
    durably one-by-one. The enclosing Runtime step remains PARTIAL until every
    required object is committed and Coverage/Merge/Evidence gates pass.

    Business parsing, object schema and model prompting stay outside Runtime.
    The adapter only requires a provider callable that returns object candidates.
    """

    WORKFLOW_ID = "major_issue_d01_v1"
    AGENT_ID = "major_issue.d01.extract"
    STEP_ID = "d01_extract"

    def __init__(
        self,
        runtime: LightweightExecutionEngine,
        store: SqliteTaskStore,
        provider: MajorIssueProvider,
        *,
        max_provider_calls: int = 4,
    ):
        self.runtime = runtime
        self.store = store
        self.provider = provider
        self.max_provider_calls = max(2, int(max_provider_calls))
        self.partial_committer = PartialResultCommitter()
        self.coverage_calculator = CoverageCalculator()
        self.gate_evaluator = CompletenessGateEvaluator()

        step_policy = ExecutionPolicy(
            mode=ExecutionMode.SINGLE,
            transport_retry=RetryPolicy(max_attempts=1),
            validation_retry=RetryPolicy(max_attempts=1),
            step_retry=RetryPolicy(max_attempts=1),
            retry_budget=RetryBudget(
                max_provider_calls_per_step=self.max_provider_calls,
                max_step_attempts=1,
                max_validation_cycles_per_step_attempt=1,
                max_transport_attempts_per_model_call=1,
            ),
            failure_policy=FailurePolicy.PARTIAL,
        )
        definition = AgentDefinition(
            agent_id=self.AGENT_ID,
            label="Major Issue D01 Structured Output",
            output_schema="MajorIssueObjectCandidate[]",
            content_strategy_ref="major_issue_d01@1",
            metadata={
                "business_domain": "MAJOR_CASE",
                "fixture": "D01",
                "object_level_partial_commit": True,
            },
        )
        workflow = WorkflowDefinition(
            workflow_id=self.WORKFLOW_ID,
            version="1",
            steps=[
                StepDefinition(
                    step_id=self.STEP_ID,
                    agent_id=self.AGENT_ID,
                    execution_policy=step_policy,
                    required_for_completion=True,
                )
            ],
            failure_policy=FailurePolicy.PARTIAL,
            metadata={"business_domain": "MAJOR_CASE", "fixture": "D01"},
        )
        self.runtime.register_agent(
            self.AGENT_ID,
            self._handler,
            definition,
        )
        self.runtime.register_workflow(workflow)
        self.workflow = workflow
        self.execution_policy = ExecutionPolicy(
            mode=ExecutionMode.SEQUENTIAL,
            failure_policy=FailurePolicy.PARTIAL,
            retry_budget=RetryBudget(
                max_provider_calls_per_step=self.max_provider_calls,
            ),
        )

    @staticmethod
    def _object_execution_key(
        task_id: str,
        partition_key: str,
        object_id: str,
    ) -> str:
        return "d01-object-" + _hash(
            {
                "task_id": task_id,
                "partition_key": partition_key,
                "object_id": object_id,
            }
        )

    @staticmethod
    def _merge_key(task_id: str, partition_key: str) -> str:
        return "d01-merge-" + _hash(
            {"task_id": task_id, "partition_key": partition_key}
        )

    @staticmethod
    def _expected_specs(payload: dict[str, Any]) -> list[MajorIssueObjectSpec]:
        return [
            MajorIssueObjectSpec.model_validate(item)
            for item in payload.get("expected_objects") or []
        ]

    @staticmethod
    def _source(payload: dict[str, Any]) -> SourceRef:
        return SourceRef.model_validate(payload["source"])

    @staticmethod
    def _partial_object_id(partial: CommittedPartialResult) -> str:
        return str(partial.metadata.get("object_id") or "")

    def _ordered_partials(
        self,
        task_id: str,
        partition_key: str,
        specs: list[MajorIssueObjectSpec],
    ) -> list[CommittedPartialResult]:
        order = {item.object_id: index for index, item in enumerate(specs)}
        partials = self.store.list_partial_results(
            task_id,
            partition_key=partition_key,
        )
        return sorted(
            partials,
            key=lambda item: order.get(
                self._partial_object_id(item),
                len(order),
            ),
        )

    @staticmethod
    def _save_evidence_topologically(
        registry: EvidenceRegistry,
        evidence_items: list[EvidenceReference],
    ) -> None:
        remaining = {item.evidence_id: item for item in evidence_items}
        while remaining:
            progressed = False
            for evidence_id, item in list(remaining.items()):
                if all(
                    parent not in remaining
                    for parent in item.derived_from
                ):
                    registry.save(item)
                    remaining.pop(evidence_id)
                    progressed = True
            if not progressed:
                raise RuntimeStepError(
                    "evidence lineage cannot be resolved",
                    code="D01_EVIDENCE_LINEAGE_INVALID",
                    category=ErrorCategory.BUSINESS,
                    retryable=False,
                    details={"evidence_ids": sorted(remaining)},
                )

    def _finalize(
        self,
        *,
        task_id: str,
        partition_key: str,
        specs: list[MajorIssueObjectSpec],
        source: SourceRef,
    ) -> dict[str, Any]:
        partials = self._ordered_partials(task_id, partition_key, specs)
        expected_ids = [item.object_id for item in specs]
        present_ids = {
            self._partial_object_id(item)
            for item in partials
        }
        missing_ids = [
            object_id
            for object_id in expected_ids
            if object_id not in present_ids
        ]
        if missing_ids:
            raise RuntimeStepError(
                "D01 structured output is incomplete",
                code="D01_OUTPUT_INCOMPLETE",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={"missing_object_ids": missing_ids},
            )

        coverage_universe = CoverageUniverse(
            source=source,
            coverage_type="ITEM",
            unit_targets=[
                CoverageUnit(
                    unit_id=spec.unit_id,
                    locator=spec.locator,
                )
                for spec in specs
            ],
            universe_fingerprint=_hash(
                {
                    "source_fingerprint": source.fingerprint,
                    "partition_key": partition_key,
                    "unit_ids": [item.unit_id for item in specs],
                }
            ),
            partition_key=partition_key,
        )
        coverage = self.coverage_calculator.calculate(
            coverage_universe,
            processed_unit_ids=[
                unit_id
                for partial in partials
                for unit_id in partial.unit_ids
            ],
        )

        registry = EvidenceRegistry()
        all_evidence: list[EvidenceReference] = []
        for partial in partials:
            for item in partial.metadata.get("evidence") or []:
                all_evidence.append(EvidenceReference.model_validate(item))
        self._save_evidence_topologically(registry, all_evidence)

        raw_evidence_ids = [
            item.evidence_id
            for item in all_evidence
        ]
        lineage_ids = list(dict.fromkeys(raw_evidence_ids))
        if raw_evidence_ids:
            merge_evidence_id = (
                "evidence-d01-merge-"
                + _hash(
                    {
                        "task_id": task_id,
                        "partition_key": partition_key,
                        "evidence_ids": sorted(raw_evidence_ids),
                    }
                )
            )
            registry.derive(
                evidence_id=merge_evidence_id,
                source=source,
                locator=EvidenceLocator(
                    type="EVENT",
                    value={
                        "kind": "D01_MERGE",
                        "partition_key": partition_key,
                    },
                ),
                derived_from=list(dict.fromkeys(raw_evidence_ids)),
                partition_key=partition_key,
            )
            lineage_ids = [
                item.evidence_id
                for item in registry.lineage(merge_evidence_id)
            ]

        evidence_integrity = registry.validate_integrity(
            list(dict.fromkeys(raw_evidence_ids))
            if raw_evidence_ids
            else None
        )

        merge_context = MergeContext(
            merge_key=self._merge_key(task_id, partition_key),
            expected_partial_ids=[
                partial.partial_id for partial in partials
            ],
            partition_key=partition_key,
            partition_policy="ISOLATED",
            metadata={
                "business_domain": "MAJOR_CASE",
                "fixture": "D01",
            },
        )
        merge = MergeCoordinator(self.store).merge_and_commit(
            ListResultMerger(
                registry if raw_evidence_ids else None
            ),
            partials,
            merge_context,
        )
        gate = self.gate_evaluator.evaluate(
            coverage=coverage,
            schema_valid=True,
            merge_result=merge,
            evidence_integrity=evidence_integrity,
            business_gate_passed=True,
            gate_ref="MAJOR_ISSUE_D01",
            gate_version="1",
        )
        if not gate.passed:
            raise RuntimeStepError(
                "D01 completeness gate failed",
                code="D01_COMPLETENESS_GATE_FAILED",
                category=ErrorCategory.BUSINESS,
                retryable=False,
                details={"reasons": gate.reasons},
            )

        return {
            "partition_key": partition_key,
            "committed_objects": [
                {
                    "object_id": self._partial_object_id(partial),
                    "partial_id": partial.partial_id,
                    "execution_key": partial.execution_key,
                    "unit_ids": partial.unit_ids,
                    "data": partial.data,
                    "evidence_ids": partial.evidence_ids,
                }
                for partial in partials
            ],
            "coverage": coverage.model_dump(mode="json"),
            "merge": merge.model_dump(mode="json"),
            "gate": gate.model_dump(mode="json"),
            "evidence_integrity": evidence_integrity,
            "evidence_lineage_ids": lineage_ids,
        }

    def _handler(
        self,
        business_input: Any,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(business_input or {})
        runtime_context = dict(context.get("runtime") or {})
        task_id = str(runtime_context["task_id"])
        run_id = str(runtime_context["run_id"])
        step_run_id = str(runtime_context["step_run_id"])
        partition_key = str(payload.get("partition_key") or "__default__")
        specs = self._expected_specs(payload)
        if not specs:
            raise RuntimeStepError(
                "D01 expected_objects is empty",
                code="D01_EXPECTED_OBJECTS_EMPTY",
                category=ErrorCategory.BUSINESS,
                retryable=False,
            )
        source = self._source(payload)

        existing = self._ordered_partials(
            task_id,
            partition_key,
            specs,
        )
        committed_ids = {
            self._partial_object_id(item)
            for item in existing
        }
        pending_specs = [
            spec
            for spec in specs
            if spec.object_id not in committed_ids
        ]

        if pending_specs:
            provider_context = {
                **context,
                "d01": {
                    "partition_key": partition_key,
                    "committed_object_ids": sorted(committed_ids),
                    "pending_object_ids": [
                        item.object_id for item in pending_specs
                    ],
                },
            }
            raw_candidates = self.provider(
                dict(payload.get("provider_input") or {}),
                [
                    item.model_dump(mode="json")
                    for item in pending_specs
                ],
                provider_context,
            )
            expected_by_id = {
                item.object_id: item
                for item in pending_specs
            }

            for raw in raw_candidates:
                candidate = (
                    raw
                    if isinstance(raw, MajorIssueObjectCandidate)
                    else MajorIssueObjectCandidate.model_validate(raw)
                )
                if candidate.object_id in committed_ids:
                    continue
                spec = expected_by_id.get(candidate.object_id)
                if spec is None:
                    raise RuntimeStepError(
                        "provider returned an object that cannot bind to execution_key",
                        code="D01_UNBOUND_OBJECT",
                        category=ErrorCategory.VALIDATION,
                        retryable=False,
                        details={"object_id": candidate.object_id},
                    )

                execution_key = self._object_execution_key(
                    task_id,
                    partition_key,
                    candidate.object_id,
                )
                partial_candidate = PartialResultCandidate(
                    chunk_id=f"d01:{partition_key}:{candidate.object_id}",
                    execution_key=execution_key,
                    unit_ids=[spec.unit_id],
                    data=candidate.data,
                    partition_key=partition_key,
                    schema_valid=candidate.schema_valid,
                    complete_object=candidate.complete_object,
                    finish_reason=candidate.finish_reason,
                    evidence_ids=[
                        item.evidence_id for item in candidate.evidence
                    ],
                    metadata={
                        **candidate.metadata,
                        "object_id": candidate.object_id,
                        "locator": spec.locator,
                        "evidence": [
                            item.model_dump(mode="json")
                            for item in candidate.evidence
                        ],
                    },
                )
                try:
                    committed = self.partial_committer.commit(
                        partial_candidate
                    )
                except InvalidPartialResultError as exc:
                    raise RuntimeStepError(
                        str(exc),
                        code=(
                            "D01_OUTPUT_TRUNCATED"
                            if not candidate.complete_object
                            else "D01_SCHEMA_INVALID"
                        ),
                        category=ErrorCategory.VALIDATION,
                        retryable=True,
                        details={
                            "object_id": candidate.object_id,
                            "finish_reason": candidate.finish_reason,
                        },
                    ) from exc

                self.store.commit_partial_result(
                    task_id=task_id,
                    run_id=run_id,
                    step_run_id=step_run_id,
                    partial=committed,
                )
                committed_ids.add(candidate.object_id)

        return self._finalize(
            task_id=task_id,
            partition_key=partition_key,
            specs=specs,
            source=source,
        )

    def execute_partition(
        self,
        *,
        case_id: str,
        issue_version_id: str,
        partition_key: str,
        source: SourceRef,
        expected_objects: list[MajorIssueObjectSpec | dict[str, Any]],
        provider_input: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> MajorIssueD01Outcome:
        specs = [
            item
            if isinstance(item, MajorIssueObjectSpec)
            else MajorIssueObjectSpec.model_validate(item)
            for item in expected_objects
        ]
        identity = _hash(
            {
                "case_id": case_id,
                "issue_version_id": issue_version_id,
                "partition_key": partition_key,
                "source_fingerprint": source.fingerprint,
                "expected_objects": [
                    item.model_dump(mode="json")
                    for item in specs
                ],
                "provider_input": provider_input or {},
            }
        )[:16]
        request = WorkflowRequest(
            request_id=request_id
            or (
                f"major-d01:{case_id}:{issue_version_id}:"
                f"{partition_key}:{identity}"
            ),
            workflow_id=self.WORKFLOW_ID,
            input={
                "case_id": case_id,
                "issue_version_id": issue_version_id,
                "partition_key": partition_key,
                "source": source.model_dump(mode="json"),
                "expected_objects": [
                    item.model_dump(mode="json")
                    for item in specs
                ],
                "provider_input": provider_input or {},
            },
            execution_policy=self.execution_policy,
            metadata={
                "business_domain": "MAJOR_CASE",
                "business_id": case_id,
                "issue_version_id": issue_version_id,
                "partition_key": partition_key,
                "fixture": "D01",
            },
        )
        result = self.runtime.execute(request)
        return self.outcome(result.task_id)

    def resume(self, task_id: str) -> MajorIssueD01Outcome:
        self.runtime.resume(task_id)
        return self.outcome(task_id)

    def outcome(self, task_id: str) -> MajorIssueD01Outcome:
        snapshot = self.runtime.get_task(task_id)
        request = self.store.load_request(task_id)
        if not isinstance(request, WorkflowRequest):
            raise TypeError("D01 task must be a WorkflowRequest")
        payload = dict(request.input or {})
        partition_key = str(payload.get("partition_key") or "__default__")
        specs = self._expected_specs(payload)
        source = self._source(payload)
        partials = self._ordered_partials(
            task_id,
            partition_key,
            specs,
        )
        coverage = self.coverage_calculator.calculate(
            CoverageUniverse(
                source=source,
                coverage_type="ITEM",
                unit_targets=[
                    CoverageUnit(
                        unit_id=spec.unit_id,
                        locator=spec.locator,
                    )
                    for spec in specs
                ],
                universe_fingerprint=_hash(
                    {
                        "source_fingerprint": source.fingerprint,
                        "partition_key": partition_key,
                        "unit_ids": [item.unit_id for item in specs],
                    }
                ),
                partition_key=partition_key,
            ),
            processed_unit_ids=[
                unit_id
                for partial in partials
                for unit_id in partial.unit_ids
            ],
        )
        merge = self.store.get_merge_result(
            self._merge_key(task_id, partition_key)
        )
        gate = None
        evidence_integrity = True
        evidence_lineage_ids: list[str] = []

        if snapshot.result is not None and snapshot.result.data:
            step_data = snapshot.result.data.get(self.STEP_ID)
            if isinstance(step_data, dict):
                if step_data.get("gate"):
                    gate = CompletenessGateResult.model_validate(
                        step_data["gate"]
                    )
                evidence_integrity = bool(
                    step_data.get("evidence_integrity", True)
                )
                evidence_lineage_ids = list(
                    step_data.get("evidence_lineage_ids") or []
                )

        provider_calls = self.store.count_task_provider_calls(task_id)
        return MajorIssueD01Outcome(
            task_id=task_id,
            run_id=snapshot.current_run_id,
            status=snapshot.status,
            partition_key=partition_key,
            committed_objects=[
                {
                    "object_id": self._partial_object_id(partial),
                    "partial_id": partial.partial_id,
                    "execution_key": partial.execution_key,
                    "unit_ids": partial.unit_ids,
                    "data": partial.data,
                    "evidence_ids": partial.evidence_ids,
                }
                for partial in partials
            ],
            coverage=coverage,
            merge=merge,
            gate=gate,
            evidence_integrity=evidence_integrity,
            evidence_lineage_ids=evidence_lineage_ids,
            business_consumable=bool(
                snapshot.status == RuntimeStatus.COMPLETED
                and gate is not None
                and gate.passed
            ),
            provider_calls=provider_calls,
        )


class RepeatCaseRuntimeAdapter:
    """Opaque Runtime bridge for Repeat Case business logic.

    Runtime owns reliable execution only. Retrieval, ranking, threshold and
    judgement schemas remain fully inside the supplied business agent.
    """

    AGENT_ID = "major_issue.repeat_case"

    def __init__(
        self,
        runtime: LightweightExecutionEngine,
        agent: RepeatCaseAgent,
    ):
        self.runtime = runtime
        self.agent = agent
        definition = AgentDefinition(
            agent_id=self.AGENT_ID,
            label="Repeat Case Business Agent",
            metadata={
                "business_domain": "MAJOR_CASE",
                "opaque_business_payload": True,
            },
        )
        self.runtime.register_agent(
            self.AGENT_ID,
            self._handler,
            definition,
        )

    def _handler(self, payload: Any, context: dict[str, Any]) -> Any:
        return self.agent(payload, context)

    def execute(
        self,
        payload: Any,
        *,
        request_id: str,
        partition_key: str | None = None,
    ):
        return self.runtime.invoke(
            AgentRequest(
                request_id=request_id,
                agent_id=self.AGENT_ID,
                input=payload,
                metadata={
                    "business_domain": "MAJOR_CASE",
                    "partition_key": partition_key,
                },
            )
        )
