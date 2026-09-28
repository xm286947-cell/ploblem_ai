"""QS-MVP-03 Candidate V1 production service.

This service is the product entry point for deterministic
ReverseQualityResult -> ScenarioCandidateV1 -> QualityScenarioV1(CANDIDATE)
production. It never invokes AI and it does not change Reverse Quality
semantics.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from quality_knowledge.quality_scenario_v1 import (
    QualityScenarioV1,
    ScenarioCandidateV1,
    ScenarioStatus,
    ScenarioTriggerSource,
)
from quality_knowledge.quality_scenario_v1_adapter import (
    scenario_candidate_v1_from_reverse_quality,
)
from quality_knowledge.quality_scenario_v1_store import QualityScenarioV1Repository


@dataclass(frozen=True)
class CandidateV1ProductionResult:
    candidate: ScenarioCandidateV1
    scenario: QualityScenarioV1
    idempotency_key: str
    created: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "idempotency_key": self.idempotency_key,
            "candidate": self.candidate.model_dump(mode="json"),
            "scenario": self.scenario.model_dump(mode="json"),
        }


class CandidateV1Service:
    """Create and read Quality Scenario V1 candidates.

    Idempotency scope is the accepted Reverse Quality run plus the explicit
    business trigger context. Repeating the same input returns the already
    persisted scenario instead of overwriting it. A later Review/Confirm step
    is therefore protected from a repeated production request.
    """

    def __init__(self, repository: QualityScenarioV1Repository):
        self.repository = repository

    def build_candidate(
        self,
        result: dict[str, Any],
        taxonomy: dict[str, Any],
        *,
        trigger_source: ScenarioTriggerSource | str | None = None,
        trigger_reason: str = "",
    ) -> ScenarioCandidateV1:
        return scenario_candidate_v1_from_reverse_quality(
            result,
            taxonomy,
            trigger_source=trigger_source,
            trigger_reason=trigger_reason,
        )

    @staticmethod
    def _idempotency_key(candidate: ScenarioCandidateV1) -> str:
        trigger = candidate.trigger_source.value if candidate.trigger_source else ""
        material = "|".join(
            [
                candidate.source_result_version,
                candidate.source_analysis_id,
                candidate.source_run_id,
                str(candidate.source_run_seq),
                candidate.candidate_id,
                trigger,
                candidate.trigger_reason.strip(),
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @classmethod
    def scenario_id_for(cls, candidate: ScenarioCandidateV1) -> str:
        return "QSV1C-" + cls._idempotency_key(candidate)[:24]

    def create_from_reverse(
        self,
        result: dict[str, Any],
        taxonomy: dict[str, Any],
        *,
        trigger_source: ScenarioTriggerSource | str | None = None,
        trigger_reason: str = "",
        created_by: str = "",
    ) -> CandidateV1ProductionResult:
        candidate = self.build_candidate(
            result,
            taxonomy,
            trigger_source=trigger_source,
            trigger_reason=trigger_reason,
        )
        idempotency_key = self._idempotency_key(candidate)
        scenario_id = self.scenario_id_for(candidate)

        existing = self.repository.get(scenario_id)
        if existing is not None:
            return CandidateV1ProductionResult(
                candidate=candidate,
                scenario=existing,
                idempotency_key=idempotency_key,
                created=False,
            )

        saved = self.repository.create_from_candidate(
            candidate,
            scenario_id=scenario_id,
            created_by=created_by or candidate.producer,
        )
        return CandidateV1ProductionResult(
            candidate=candidate,
            scenario=saved,
            idempotency_key=idempotency_key,
            created=True,
        )

    def get_candidate(self, scenario_id: str) -> QualityScenarioV1 | None:
        item = self.repository.get(scenario_id)
        if item is None or item.status != ScenarioStatus.CANDIDATE:
            return None
        return item

    def list_candidates(
        self,
        *,
        product_code: str = "",
        lifecycle_stage_code: str = "",
        business_activity_code: str = "",
        quality_concern_code: str = "",
        q: str = "",
    ) -> list[QualityScenarioV1]:
        return self.repository.list(
            product_code=product_code,
            lifecycle_stage_code=lifecycle_stage_code,
            business_activity_code=business_activity_code,
            quality_concern_code=quality_concern_code,
            status=ScenarioStatus.CANDIDATE,
            q=q,
        )


__all__ = [
    "CandidateV1ProductionResult",
    "CandidateV1Service",
]
