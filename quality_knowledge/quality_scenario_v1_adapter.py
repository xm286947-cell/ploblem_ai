"""Compatibility adapters for the frozen Quality Scenario V1 contract.

Reverse mapping reuses the already-accepted ReverseQualityResult adapter and
never starts a second AI analysis.  Legacy mapping is display/read compatibility
only; it does not migrate legacy rows into the V1 source of truth.
"""
from __future__ import annotations

import hashlib
from typing import Any

from quality_knowledge.quality_scenario_v1 import (
    SCHEMA_VERSION,
    ScenarioCandidateV1,
    ScenarioEvidenceReference,
    ScenarioMissingInformation,
    ScenarioProvenanceType,
    ScenarioRelationType,
    ScenarioSourceReference,
    ScenarioStatus,
    ScenarioTriggerSource,
)
from quality_knowledge.reverse_quality_scenario_adapter import adapt_reverse_quality_result


def _provenance(value: str) -> ScenarioProvenanceType:
    source = str(value or "").strip().upper()
    if "HUMAN" in source or "CONFIRMED" in source:
        return ScenarioProvenanceType.HUMAN_CONFIRMED
    if source in {
        "FACT", "ITR_CS", "ITR_SOURCE", "SOFTWARE_OPERATION",
        "STRUCTURED", "SOURCE_FACT",
    } or source.startswith("SOURCE_"):
        return ScenarioProvenanceType.FACT
    return ScenarioProvenanceType.INFERRED


def _support_path(candidate_field: str) -> str:
    return {
        "customer_perception": "scenario_description",
        "experience_requirement": "expected_result",
        "quality_attribute": "quality_concern_name",
        "quality_subcharacteristic": "quality_concern_name",
        "failure_mode": "scenario_description",
        "failure_mechanism": "scenario_description",
        "trigger_conditions": "trigger_condition",
        "preconditions": "trigger_condition",
        "operating_environment": "applicability_scope",
        "operating_condition": "applicability_scope",
        "participating_systems": "applicability_scope",
        "system_scale": "applicability_scope",
        "business_impact": "scenario_description",
        "recovery_method": "expected_result",
    }.get(candidate_field, candidate_field)


def scenario_candidate_v1_from_reverse_quality(
    result: dict[str, Any],
    taxonomy: dict[str, Any],
    *,
    trigger_source: ScenarioTriggerSource | str | None = None,
    trigger_reason: str = "",
) -> ScenarioCandidateV1:
    """Map an accepted ReverseQualityResult to Candidate V1.

    Trigger context belongs to the upstream business trigger, not to
    ReverseQualityResult. Missing trigger context is therefore surfaced as a
    Candidate blocker instead of being guessed from reverse-analysis content.
    """
    adapted = adapt_reverse_quality_result(result, taxonomy)
    normalized_trigger = (
        ScenarioTriggerSource(trigger_source) if trigger_source else None
    )
    normalized_reason = str(trigger_reason or "").strip()
    old = adapted.candidate

    source_ref = f"ITR:{adapted.canonical_itr}" if adapted.canonical_itr else "ITR:UNRESOLVED"
    source = ScenarioSourceReference(
        source_ref=source_ref,
        source_type="ITR",
        source_id=adapted.canonical_itr or "UNRESOLVED",
        canonical_itr=adapted.canonical_itr,
        product_code=adapted.product_code,
        relation_type=ScenarioRelationType.PRIMARY,
    )

    evidence_supports: dict[str, set[str]] = {}
    evidence_provenance: dict[str, ScenarioProvenanceType] = {}
    for candidate_field, meta in adapted.field_evidence.items():
        for evidence_id in meta.get("evidence_ids") or []:
            evidence_id = str(evidence_id).strip()
            if not evidence_id:
                continue
            evidence_supports.setdefault(evidence_id, set()).add(_support_path(candidate_field))
            provenance = _provenance(str(meta.get("source_type") or ""))
            current = evidence_provenance.get(evidence_id)
            if current != ScenarioProvenanceType.HUMAN_CONFIRMED:
                evidence_provenance[evidence_id] = provenance

    evidence_refs = [
        ScenarioEvidenceReference(
            evidence_id=evidence_id,
            source_ref=source_ref,
            evidence_type="REVERSE_QUALITY_FIELD_EVIDENCE",
            content_ref=(
                f"reverse-quality://{adapted.source_analysis_id or 'analysis'}/"
                f"{adapted.source_run_id or 'run'}/{evidence_id}"
            ),
            supports=sorted(supports),
            source_type=evidence_provenance.get(
                evidence_id, ScenarioProvenanceType.INFERRED
            ),
        )
        for evidence_id, supports in sorted(evidence_supports.items())
    ]

    lifecycle = next(
        (
            row for row in taxonomy.get("lifecycles", [])
            if row.get("lifecycle_code") == old.get("lifecycle_code")
        ),
        {},
    )
    activity = next(
        (
            row for row in taxonomy.get("activities", [])
            if row.get("activity_code") == old.get("activity_code")
        ),
        {},
    )
    concern_code = str(old.get("primary_quality_concern_code") or "").strip()
    concern_name = str(old.get("concern_points") or old.get("quality_attribute") or "").strip()
    expected_result = str(old.get("experience_requirement") or "").strip()

    description_parts = [
        str(old.get("customer_perception") or "").strip(),
        str(old.get("failure_mode") or "").strip(),
        str(old.get("business_impact") or "").strip(),
    ]
    scenario_description = "；".join(dict.fromkeys(x for x in description_parts if x))[:4000]

    blockers = list(adapted.blockers)
    if not concern_code and not concern_name:
        blockers.append("QUALITY_CONCERN_REQUIRED")
    if not expected_result:
        blockers.append("EXPECTED_RESULT_REQUIRED")
    if not evidence_refs:
        blockers.append("EVIDENCE_REQUIRED")
    if normalized_trigger is None:
        blockers.append("TRIGGER_SOURCE_REQUIRED")
    if not normalized_reason:
        blockers.append("TRIGGER_REASON_REQUIRED")

    missing = []
    payload = result.get("result") if isinstance(result.get("result"), dict) else result
    for item in payload.get("missing_information") or []:
        if not isinstance(item, dict) or not str(item.get("question") or "").strip():
            continue
        missing.append(
            ScenarioMissingInformation(
                field_name=str(item.get("field_name") or ""),
                reason=str(item.get("reason") or ""),
                question=str(item.get("question") or ""),
                evidence_needed=[
                    str(x) for x in (item.get("evidence_needed") or []) if str(x).strip()
                ],
                status=(
                    item.get("status")
                    if item.get("status") in {"PENDING", "CONFIRMED", "NOT_APPLICABLE"}
                    else "PENDING"
                ),
                answer=str(item.get("answer") or ""),
            )
        )

    key = "|".join(
        [
            adapted.canonical_itr,
            adapted.source_analysis_id,
            adapted.source_run_id,
            str(adapted.source_run_seq),
        ]
    )
    candidate_id = "QSCAND-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]

    return ScenarioCandidateV1(
        schema_version=SCHEMA_VERSION,
        candidate_id=candidate_id,
        status=ScenarioStatus.CANDIDATE,
        product_code=adapted.product_code,
        product_name="",
        lifecycle_stage_code=str(old.get("lifecycle_code") or ""),
        lifecycle_stage_name=str(lifecycle.get("label_zh") or ""),
        business_activity_code=str(old.get("activity_code") or ""),
        business_activity_name=str(activity.get("label_zh") or ""),
        business_goal=str(activity.get("objective") or ""),
        scenario_name=str(old.get("name") or "").strip() or candidate_id,
        scenario_description=scenario_description,
        quality_concern_code=concern_code,
        quality_concern_name=concern_name,
        trigger_source=normalized_trigger,
        trigger_reason=normalized_reason,
        trigger_condition="；".join(
            dict.fromkeys(
                x for x in (
                    str(old.get("preconditions") or "").strip(),
                    str(old.get("trigger_conditions") or "").strip(),
                ) if x
            )
        )[:2000],
        expected_result=expected_result,
        applicability_scope="；".join(
            dict.fromkeys(
                x for x in (
                    str(old.get("applicable_boundary") or "").strip(),
                    str(old.get("operating_environment") or "").strip(),
                    str(old.get("operating_condition") or "").strip(),
                    str(old.get("participating_systems") or "").strip(),
                    str(old.get("system_scale") or "").strip(),
                ) if x
            )
        )[:2000],
        source_problem_refs=[source],
        evidence_refs=evidence_refs,
        blockers=list(dict.fromkeys(blockers)),
        missing_information=missing,
        source_result_version=adapted.source_result_version,
        source_analysis_id=adapted.source_analysis_id,
        source_run_id=adapted.source_run_id,
        source_run_seq=adapted.source_run_seq,
        producer=adapted.adapter_version,
        field_evidence=adapted.field_evidence,
    )


def legacy_scenario_to_v1_view(legacy: dict[str, Any]) -> dict[str, Any]:
    """Map a legacy scenario to a read-only V1-shaped view.

    This is intentionally not a QualityScenarioV1 migration.  Missing source /
    evidence and semantic differences stay visible to callers.
    """
    legacy_status = str(legacy.get("status") or "DRAFT")
    mapped_status = {
        "PUBLISHED": ScenarioStatus.PUBLISHED.value,
        "RETIRED": ScenarioStatus.REJECTED.value,
        "DRAFT": ScenarioStatus.CANDIDATE.value,
        "IN_REVIEW": ScenarioStatus.CANDIDATE.value,
    }.get(legacy_status, ScenarioStatus.CANDIDATE.value)
    return {
        "schema_version": SCHEMA_VERSION,
        "read_only": True,
        "compatibility_source": "legacy-quality-scenario",
        "legacy_status": legacy_status,
        "scenario_id": str(legacy.get("scenario_id") or ""),
        "scenario_version": int(legacy.get("version_no") or 1),
        "status": mapped_status,
        "product_code": str(legacy.get("product_code") or ""),
        "product_name": "",
        "lifecycle_stage_code": str(legacy.get("lifecycle_code") or ""),
        "lifecycle_stage_name": str(legacy.get("lifecycle_label") or ""),
        "business_activity_code": str(legacy.get("activity_code") or ""),
        "business_activity_name": str(legacy.get("activity_label") or ""),
        "business_goal": "",
        "scenario_name": str(legacy.get("name") or ""),
        "scenario_description": str(legacy.get("customer_perception") or ""),
        "quality_concern_code": str(legacy.get("primary_quality_concern_code") or ""),
        "quality_concern_name": str(
            legacy.get("concern_points") or legacy.get("quality_attribute") or ""
        ),
        "trigger_source": None,
        "trigger_reason": "",
        "trigger_condition": str(
            legacy.get("trigger_conditions") or legacy.get("preconditions") or ""
        ),
        "expected_result": str(legacy.get("experience_requirement") or ""),
        "applicability_scope": str(legacy.get("applicable_boundary") or ""),
        "source_problem_refs": [],
        "evidence_refs": [],
        "confirmation": {
            "quality_confirmed_by": "",
            "quality_confirmed_at": "",
            "technical_confirmed_by": "",
            "technical_confirmed_at": "",
            "confirmation_note": "",
        },
        "compatibility_warnings": [
            "LEGACY_READ_ONLY",
            "SOURCE_EVIDENCE_REQUIRES_EXPLICIT_V1_MIGRATION",
            "TRIGGER_CONTEXT_REQUIRES_EXPLICIT_V1_INPUT",
            "DUAL_CONFIRMATION_REQUIRES_EXPLICIT_V1_REVIEW",
        ],
    }


__all__ = [
    "scenario_candidate_v1_from_reverse_quality",
    "legacy_scenario_to_v1_view",
]
