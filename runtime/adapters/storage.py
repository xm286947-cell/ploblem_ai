from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from runtime.content import (
    ContentPlanner,
    SourceIdentityProvider,
)
from runtime.contracts import (
    AgentDefinition,
    AgentRequest,
    AtomicGroup,
    AtomicGroupPolicy,
    ContentSource,
    EvidenceReference,
    LogicalUnit,
    LongContentPolicy,
    RuntimeStatus,
    SourceBundle,
    SourceRef,
)
from runtime.engine import LightweightExecutionEngine


class StorageFieldResult(BaseModel):
    field_id: str
    status: Literal["FOUND", "MISSING", "CONFLICT"]
    normalized_value: Any = None
    unit: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    review_reason: str | None = None
    conflict_reason: str | None = None
    missing_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StorageGoldenDiff(BaseModel):
    field_id: str
    attribute: str
    expected: Any = None
    actual: Any = None
    accepted: bool = False
    explanation: str | None = None


class StorageGoldenDiffReport(BaseModel):
    diffs: list[StorageGoldenDiff]
    summary: dict[str, int]
    passed: bool


class StorageGoldenDiffError(AssertionError):
    def __init__(self, report: StorageGoldenDiffReport):
        super().__init__(
            "unexplained Storage golden field-level differences: "
            + ", ".join(
                f"{item.field_id}.{item.attribute}"
                for item in report.diffs
                if not item.accepted
            )
        )
        self.report = report


class StorageLinkedFieldsProjector:
    """Business-owned linked-field projection for Storage fixtures."""

    COMPONENTS = ("value", "unit", "qualifier", "footnote")

    def project(
        self,
        *,
        source: SourceRef,
        fields: list[dict[str, Any]],
        partition_key: str | None = None,
    ) -> SourceBundle:
        units: list[LogicalUnit] = []
        groups: list[AtomicGroup] = []
        for field in fields:
            field_id = str(field["field_id"])
            unit_ids: list[str] = []
            for component in self.COMPONENTS:
                if component not in field:
                    continue
                unit_id = f"{field_id}:{component}"
                unit_ids.append(unit_id)
                units.append(
                    LogicalUnit(
                        unit_id=unit_id,
                        source_id=source.source_id,
                        locator={
                            "field_id": field_id,
                            "component": component,
                        },
                        inline_payload=field.get(component),
                        group_id=field_id,
                        partition_key=partition_key,
                        metadata={
                            "storage_field_id": field_id,
                            "storage_component": component,
                        },
                    )
                )
            if unit_ids:
                groups.append(
                    AtomicGroup(
                        group_id=f"linked-fields:{field_id}",
                        unit_ids=unit_ids,
                        policy=AtomicGroupPolicy.KEEP_TOGETHER,
                        grouping_hint="storage_linked_fields",
                    )
                )

        return SourceBundle(
            bundle_id=f"storage:{source.source_id}:{source.fingerprint}",
            sources=[
                ContentSource(
                    source=source,
                    partition_key=partition_key,
                )
            ],
            logical_units=units,
            atomic_groups=groups,
            default_partition_key=partition_key,
            metadata={"business_domain": "STORAGE"},
        )


class StorageGoldenFieldComparator:
    ATTRIBUTES = (
        "status",
        "normalized_value",
        "unit",
        "evidence_source_fingerprint",
        "evidence_locator",
        "review_reason",
        "conflict_missing_reason",
    )

    @staticmethod
    def _field_map(
        item: StorageFieldResult,
    ) -> dict[str, Any]:
        evidence_fingerprints = [
            evidence.source.fingerprint
            for evidence in item.evidence
        ]
        evidence_locators = [
            evidence.locator.model_dump(mode="json")
            for evidence in item.evidence
        ]
        return {
            "status": item.status,
            "normalized_value": item.normalized_value,
            "unit": item.unit,
            "evidence_source_fingerprint": evidence_fingerprints,
            "evidence_locator": evidence_locators,
            "review_reason": item.review_reason,
            "conflict_missing_reason": (
                item.conflict_reason or item.missing_reason
            ),
        }

    def compare(
        self,
        expected: list[StorageFieldResult | dict[str, Any]],
        actual: list[StorageFieldResult | dict[str, Any]],
        *,
        accepted_differences: dict[str, str] | None = None,
    ) -> StorageGoldenDiffReport:
        accepted_differences = accepted_differences or {}
        expected_by_id = {
            item.field_id: item
            for raw in expected
            for item in [
                raw
                if isinstance(raw, StorageFieldResult)
                else StorageFieldResult.model_validate(raw)
            ]
        }
        actual_by_id = {
            item.field_id: item
            for raw in actual
            for item in [
                raw
                if isinstance(raw, StorageFieldResult)
                else StorageFieldResult.model_validate(raw)
            ]
        }
        diffs: list[StorageGoldenDiff] = []

        all_ids = sorted(set(expected_by_id) | set(actual_by_id))
        for field_id in all_ids:
            before = expected_by_id.get(field_id)
            after = actual_by_id.get(field_id)
            if before is None or after is None:
                key = f"{field_id}.field_presence"
                explanation = accepted_differences.get(key)
                diffs.append(
                    StorageGoldenDiff(
                        field_id=field_id,
                        attribute="field_presence",
                        expected=before is not None,
                        actual=after is not None,
                        accepted=bool(explanation),
                        explanation=explanation,
                    )
                )
                continue

            before_map = self._field_map(before)
            after_map = self._field_map(after)
            for attribute in self.ATTRIBUTES:
                if before_map[attribute] == after_map[attribute]:
                    continue
                key = f"{field_id}.{attribute}"
                explanation = accepted_differences.get(key)
                diffs.append(
                    StorageGoldenDiff(
                        field_id=field_id,
                        attribute=attribute,
                        expected=before_map[attribute],
                        actual=after_map[attribute],
                        accepted=bool(explanation),
                        explanation=explanation,
                    )
                )

        statuses = Counter(
            item.status
            for item in actual_by_id.values()
        )
        report = StorageGoldenDiffReport(
            diffs=diffs,
            summary={
                "FOUND": int(statuses.get("FOUND", 0)),
                "MISSING": int(statuses.get("MISSING", 0)),
                "CONFLICT": int(statuses.get("CONFLICT", 0)),
            },
            passed=all(item.accepted for item in diffs),
        )
        if not report.passed:
            raise StorageGoldenDiffError(report)
        return report


class StorageCompatibilityAdapter:
    """Storage business boundary around generic Runtime capabilities."""

    AGENT_ID = "storage.field_extract"

    def __init__(
        self,
        runtime: LightweightExecutionEngine,
        agent: Callable[[Any, dict[str, Any]], Any],
        *,
        missing_verification: Callable[[Any], Any] | None = None,
        normalizer: Callable[[Any], Any] | None = None,
        business_validator: Callable[[Any], Any] | None = None,
        review_gate: Callable[[Any, Any, Any], Any] | None = None,
        reviewed_specification: Callable[[Any, Any], Any] | None = None,
    ):
        self.runtime = runtime
        self.agent = agent
        self.missing_verification = missing_verification or (
            lambda value: None
        )
        self.normalizer = normalizer or (lambda value: value)
        self.business_validator = business_validator or (
            lambda value: {"valid": True}
        )
        self.review_gate = review_gate or (
            lambda value, validation, missing: {
                "accepted": True,
                "validation": validation,
                "missing": missing,
            }
        )
        self.reviewed_specification = reviewed_specification or (
            lambda value, review: {
                "value": value,
                "review": review,
            }
        )
        self.projector = StorageLinkedFieldsProjector()
        self.planner = ContentPlanner()
        self.comparator = StorageGoldenFieldComparator()

        self.runtime.register_agent(
            self.AGENT_ID,
            self._runtime_handler,
            AgentDefinition(
                agent_id=self.AGENT_ID,
                label="Storage Field Extraction",
                metadata={
                    "business_domain": "STORAGE",
                    "what_how_boundary": "RUNTIME_EXECUTION_ONLY",
                },
            ),
        )

    def _runtime_handler(
        self,
        payload: Any,
        context: dict[str, Any],
    ) -> Any:
        return self.agent(payload, context)

    def project_and_plan(
        self,
        *,
        source: SourceRef,
        fields: list[dict[str, Any]],
        policy: LongContentPolicy,
        partition_key: str | None = None,
    ):
        bundle = self.projector.project(
            source=source,
            fields=fields,
            partition_key=partition_key,
        )
        return bundle, self.planner.plan(
            bundle,
            policy,
            strategy_ref="storage_linked_fields@1",
        )

    @staticmethod
    def validate_resume_source(
        expected: SourceRef,
        actual: SourceRef,
    ) -> None:
        SourceIdentityProvider.validate_same_identity(
            expected,
            actual,
        )

    def execute(
        self,
        payload: Any,
        *,
        request_id: str,
        partition_key: str | None = None,
    ) -> dict[str, Any]:
        runtime_result = self.runtime.invoke(
            AgentRequest(
                request_id=request_id,
                agent_id=self.AGENT_ID,
                input=payload,
                metadata={
                    "business_domain": "STORAGE",
                    "partition_key": partition_key,
                },
            )
        )
        if runtime_result.status != RuntimeStatus.COMPLETED:
            return {
                "runtime_result": runtime_result,
                "reviewed_specification": None,
            }

        normalized = self.normalizer(runtime_result.data)
        missing = self.missing_verification(normalized)
        validation = self.business_validator(normalized)
        review = self.review_gate(
            normalized,
            validation,
            missing,
        )
        specification = self.reviewed_specification(
            normalized,
            review,
        )
        return {
            "runtime_result": runtime_result,
            "normalized": normalized,
            "missing_verification": missing,
            "business_validation": validation,
            "review": review,
            "reviewed_specification": specification,
        }
