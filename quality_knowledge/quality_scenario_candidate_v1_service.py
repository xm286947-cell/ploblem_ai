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
from urllib.parse import quote

from quality_knowledge.quality_scenario_v1 import (
    QualityScenarioV1,
    ScenarioCandidateV1,
    ScenarioStatus,
    ScenarioEvidenceReference,
    ScenarioProvenanceType,
    ScenarioRelationType,
    ScenarioSourceReference,
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

    @staticmethod
    def _bundle_provenance(result: dict[str, Any]) -> dict[str, Any]:
        """Resolve and validate the W2 provenance carried by a production result."""
        payloads = [result]
        for key in ("result", "input"):
            nested = result.get(key)
            if isinstance(nested, dict):
                payloads.append(nested)
                if key == "input" and isinstance(nested.get("bundle_provenance"), dict):
                    payloads.append({"bundle_provenance": nested["bundle_provenance"]})
        provenances = [
            item.get("bundle_provenance")
            for item in payloads
            if isinstance(item.get("bundle_provenance"), dict)
        ]
        if not provenances:
            raise ValueError("QSV1_BUNDLE_PROVENANCE_REQUIRED")
        provenance = provenances[0]
        bundle_id = str(provenance.get("bundle_id") or "").strip()
        revision = str(provenance.get("bundle_revision") or "").strip()
        snapshot_id = str(provenance.get("snapshot_id") or provenance.get("source_snapshot_id") or "").strip()
        selected = provenance.get("selected_issue")
        if not bundle_id or not revision or not snapshot_id or not isinstance(selected, dict):
            raise ValueError("QSV1_BUNDLE_PROVENANCE_INVALID")
        selected_id = str(
            selected.get("software_assessment_record_id")
            or selected.get("business_issue_id")
            or selected.get("knowledge_id")
            or ""
        ).strip()
        if not selected_id:
            raise ValueError("QSV1_BUNDLE_SELECTED_ISSUE_ID_REQUIRED")
        for other in provenances[1:]:
            if (
                str(other.get("bundle_id") or "").strip() != bundle_id
                or str(other.get("bundle_revision") or "").strip() != revision
                or str(other.get("snapshot_id") or other.get("source_snapshot_id") or "").strip() != snapshot_id
                or other.get("selected_issue") != selected
                or (
                    isinstance(other.get("source_refs"), list)
                    and other.get("source_refs") != provenance.get("source_refs")
                )
            ):
                raise ValueError("QSV1_BUNDLE_PROVENANCE_MISMATCH")
        identity = (result.get("result") or result).get("identity") if isinstance(result.get("result") or result, dict) else None
        if isinstance(identity, dict):
            for field, expected in (("bundle_id", bundle_id), ("bundle_revision", revision), ("source_snapshot_id", snapshot_id)):
                actual = str(identity.get(field) or "").strip()
                if actual and actual != expected:
                    raise ValueError("QSV1_BUNDLE_PROVENANCE_MISMATCH")
        return {
            "bundle_id": bundle_id,
            "bundle_revision": revision,
            "source_snapshot_id": snapshot_id,
            "selected_issue_id": selected_id,
            "selected_issue": selected,
            "source_refs": provenance.get("source_refs") if isinstance(provenance.get("source_refs"), list) else [],
        }

    @staticmethod
    def _attach_bundle_provenance(candidate: ScenarioCandidateV1, provenance: dict[str, Any]) -> ScenarioCandidateV1:
        bundle_id = provenance["bundle_id"]
        revision = provenance["bundle_revision"]
        snapshot_id = provenance["source_snapshot_id"]
        selected_id = provenance["selected_issue_id"]
        source_ref = f"QSV1-BUNDLE:{bundle_id}:{revision}"
        selected_ref = f"QSV1-SELECTED-ISSUE:{selected_id}"
        sources = list(candidate.source_problem_refs)
        for ref, source_type, source_id in (
            (source_ref, "SCENARIO_SOURCE_BUNDLE", bundle_id),
            (selected_ref, "SELECTED_ISSUE", selected_id),
            (
                f"QSV1-REVERSE-QUALITY:{candidate.source_analysis_id}:{candidate.source_run_id}",
                "REVERSE_QUALITY_RESULT",
                f"{candidate.source_analysis_id}/{candidate.source_run_id}",
            ),
        ):
            if not any(item.source_ref == ref for item in sources):
                sources.append(ScenarioSourceReference(
                    source_ref=ref,
                    source_type=source_type,
                    source_id=source_id,
                    canonical_itr=candidate.source_problem_refs[0].canonical_itr if candidate.source_problem_refs else "",
                    product_code=candidate.product_code,
                    relation_type=ScenarioRelationType.SUPPORTING,
                ))
        for index, raw_ref in enumerate(provenance.get("source_refs") or []):
            if not isinstance(raw_ref, dict):
                continue
            ref_id = str(raw_ref.get("source_ref") or raw_ref.get("source_id") or raw_ref.get("id") or "").strip()
            if not ref_id:
                continue
            ref = f"QSV1-BUNDLE-SOURCE:{index}:{ref_id}"
            if any(item.source_ref == ref for item in sources):
                continue
            sources.append(ScenarioSourceReference(
                source_ref=ref,
                source_type=str(raw_ref.get("source_type") or raw_ref.get("source_type_code") or "BUNDLE_SOURCE")[:80],
                source_id=ref_id,
                canonical_itr=candidate.source_problem_refs[0].canonical_itr if candidate.source_problem_refs else "",
                product_code=candidate.product_code,
                relation_type=ScenarioRelationType.SUPPORTING,
            ))
        locator = (
            "scenario-source-bundle://"
            + quote(bundle_id, safe="") + "/" + quote(revision, safe="")
            + "?snapshot_id=" + quote(snapshot_id, safe="")
            + "&selected_issue_id=" + quote(selected_id, safe="")
        )
        provenance_id = "BUNDLE-PROVENANCE-" + hashlib.sha256(locator.encode("utf-8")).hexdigest()[:32]
        evidence = list(candidate.evidence_refs)
        if not any(item.evidence_id == provenance_id for item in evidence):
            evidence.append(ScenarioEvidenceReference(
                evidence_id=provenance_id,
                source_ref=source_ref,
                evidence_type="QSV1_PRODUCTION_PROVENANCE",
                content_ref=locator,
                supports=["production_provenance"],
                source_type=ScenarioProvenanceType.FACT,
            ))
        result_locator = (
            f"reverse-quality://{quote(candidate.source_analysis_id or 'analysis', safe='')}/"
            f"{quote(candidate.source_run_id or 'run', safe='')}/{quote(candidate.source_result_version or 'unknown', safe='')}"
        )
        result_evidence_id = "REVERSE-QUALITY-RESULT-" + hashlib.sha256(result_locator.encode("utf-8")).hexdigest()[:24]
        if not any(item.evidence_id == result_evidence_id for item in evidence):
            evidence.append(ScenarioEvidenceReference(
                evidence_id=result_evidence_id,
                source_ref=f"QSV1-REVERSE-QUALITY:{candidate.source_analysis_id}:{candidate.source_run_id}",
                evidence_type="QSV1_REVERSE_QUALITY_RESULT",
                content_ref=result_locator,
                supports=["production_provenance"],
                source_type=ScenarioProvenanceType.FACT,
            ))
        candidate_id = "QSCAND-" + hashlib.sha256(
            f"{bundle_id}|{revision}|{candidate.trigger_source.value if candidate.trigger_source else ''}|{candidate.trigger_reason.strip()}".encode("utf-8")
        ).hexdigest()[:24]
        return candidate.model_copy(update={
            "candidate_id": candidate_id,
            "source_problem_refs": sources,
            "evidence_refs": evidence,
        })

    @staticmethod
    def _bundle_identity(candidate: ScenarioCandidateV1) -> tuple[str, str, str] | None:
        for evidence in candidate.evidence_refs:
            if evidence.evidence_type == "QSV1_PRODUCTION_PROVENANCE":
                return evidence.content_ref, candidate.trigger_source.value if candidate.trigger_source else "", candidate.trigger_reason.strip()
        return None

    @staticmethod
    def _bundle_locator(candidate: ScenarioCandidateV1) -> str:
        return next(
            evidence.content_ref
            for evidence in candidate.evidence_refs
            if evidence.evidence_type == "QSV1_PRODUCTION_PROVENANCE"
        )

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
        bundle_identity = CandidateV1Service._bundle_identity(candidate)
        if bundle_identity:
            return hashlib.sha256("|".join(bundle_identity).encode("utf-8")).hexdigest()
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
        provenance = self._bundle_provenance(result)
        candidate = self._attach_bundle_provenance(candidate, provenance)
        idempotency_key = self._idempotency_key(candidate)
        scenario_id = self.scenario_id_for(candidate)

        matches = [
            item for item in self.repository.list()
            if any(
                evidence.evidence_type == "QSV1_PRODUCTION_PROVENANCE"
                and evidence.content_ref == self._bundle_locator(candidate)
                for evidence in item.evidence_refs
            )
            and item.trigger_source == candidate.trigger_source
            and item.trigger_reason.strip() == candidate.trigger_reason.strip()
        ]
        if len(matches) > 1:
            raise ValueError("QSV1_CANDIDATE_PROVENANCE_AMBIGUOUS")
        existing = matches[0] if matches else self.repository.get(scenario_id)
        if existing is not None:
            # The preflight read is intentionally outside the repository's
            # BEGIN IMMEDIATE create transaction.  Under concurrent creation,
            # list() can observe no match and get(scenario_id) can immediately
            # observe the just-committed deterministic row.  Treat that state
            # as a normal idempotent reuse when its frozen Bundle provenance
            # and trigger identity match; only fail closed on an actual
            # deterministic-ID collision.
            if self._bundle_identity(candidate) and not matches:
                existing_locators = {
                    item.content_ref
                    for item in existing.evidence_refs
                    if item.evidence_type == "QSV1_PRODUCTION_PROVENANCE"
                }
                if self._bundle_locator(candidate) not in existing_locators:
                    raise ValueError("QSV1_CANDIDATE_IDENTITY_CONFLICT")
                if (
                    existing.trigger_source != candidate.trigger_source
                    or existing.trigger_reason.strip() != candidate.trigger_reason.strip()
                ):
                    raise ValueError("QSV1_CANDIDATE_IDENTITY_CONFLICT")
            return CandidateV1ProductionResult(
                candidate=candidate,
                scenario=existing,
                idempotency_key=idempotency_key,
                created=False,
            )

        atomic_creator = getattr(self.repository, "create_candidate_if_absent", None)
        if atomic_creator is not None:
            saved, created = atomic_creator(
                candidate,
                scenario_id=scenario_id,
                created_by=created_by or candidate.producer,
            )
            if not created:
                if (
                    self._bundle_identity(candidate)
                    and self._bundle_locator(candidate) not in {
                        item.content_ref for item in saved.evidence_refs
                        if item.evidence_type == "QSV1_PRODUCTION_PROVENANCE"
                    }
                ):
                    raise ValueError("QSV1_CANDIDATE_IDENTITY_CONFLICT")
                if saved.trigger_source != candidate.trigger_source or saved.trigger_reason.strip() != candidate.trigger_reason.strip():
                    raise ValueError("QSV1_CANDIDATE_IDENTITY_CONFLICT")
        else:
            saved = self.repository.create_from_candidate(
                candidate,
                scenario_id=scenario_id,
                created_by=created_by or candidate.producer,
            )
            created = True
        return CandidateV1ProductionResult(
            candidate=candidate,
            scenario=saved,
            idempotency_key=idempotency_key,
            created=created,
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
