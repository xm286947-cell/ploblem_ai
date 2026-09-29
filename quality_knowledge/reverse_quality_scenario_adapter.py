"""Deterministic ReverseQualityResult V0.1 -> quality scenario candidate adapter.

This module is deliberately AI-free. It converts the reviewed reverse-quality
intermediate asset into the existing scenario-candidate shape without
re-reading or re-reasoning over the original issue.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


ADAPTER_VERSION = "reverse-quality-scenario-adapter-v0.1"


@dataclass(frozen=True)
class ReverseQualityScenarioCandidate:
    adapter_version: str
    source_result_version: str
    source_analysis_id: str
    source_run_id: str
    source_run_seq: int
    canonical_itr: str
    product_code: str
    taxonomy_version_id: str
    ready: bool
    blockers: list[str]
    candidate: dict[str, Any]
    field_evidence: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    nested = result.get("result")
    return nested if isinstance(nested, dict) else result


def _field(fields: dict[str, Any], name: str) -> dict[str, Any]:
    value = fields.get(name)
    if not isinstance(value, dict) or value.get("review_status") == "REJECTED":
        return {}
    return value


def _value(fields: dict[str, Any], *names: str) -> str:
    for name in names:
        item = _field(fields, name)
        value = str(item.get("value") or "").strip()
        if value:
            return value
    return ""


def _join(values: list[str]) -> str:
    return "；".join(dict.fromkeys(x.strip() for x in values if x and x.strip()))


def adapt_reverse_quality_result(
    result: dict[str, Any],
    taxonomy: dict[str, Any],
) -> ReverseQualityScenarioCandidate:
    payload = _payload(result)
    if payload.get("result_version") != "reverse-quality-v0.1":
        raise ValueError("REVERSE_QUALITY_RESULT_VERSION_UNSUPPORTED")

    identity = payload.get("identity") or {}
    fields = payload.get("fields") or {}
    if not isinstance(identity, dict) or not isinstance(fields, dict):
        raise ValueError("REVERSE_QUALITY_RESULT_INVALID")

    product_code = str(identity.get("product_code") or "").strip()
    taxonomy_version_id = str(identity.get("taxonomy_version_id") or "").strip()
    canonical_itr = str(identity.get("canonical_itr") or "").strip()

    blockers: list[str] = []
    if not canonical_itr:
        blockers.append("CANONICAL_ITR_REQUIRED")
    if not product_code:
        blockers.append("PRODUCT_CODE_REQUIRED")
    if taxonomy_version_id and str(taxonomy.get("version_id") or "") != taxonomy_version_id:
        blockers.append("TAXONOMY_VERSION_MISMATCH")

    lifecycle_value = _value(fields, "lifecycle_stage")
    activity_value = _value(fields, "business_activity_scene")
    lifecycle_rows = [x for x in taxonomy.get("lifecycles", []) if x.get("enabled")]
    activity_rows = [x for x in taxonomy.get("activities", []) if x.get("enabled")]
    lifecycle_index = {
        str(key): row
        for row in lifecycle_rows
        for key in (row.get("lifecycle_code"), row.get("label_zh"))
        if key
    }
    activity_index = {
        str(key): row
        for row in activity_rows
        for key in (row.get("activity_code"), row.get("label_zh"))
        if key
    }

    lifecycle_row = lifecycle_index.get(lifecycle_value)
    activity_row = activity_index.get(activity_value)
    if not activity_row:
        blockers.append("ACTIVITY_NOT_MAPPED")
    if not lifecycle_row and activity_row:
        lifecycle_row = lifecycle_index.get(str(activity_row.get("lifecycle_code") or ""))
    if not lifecycle_row:
        blockers.append("LIFECYCLE_NOT_MAPPED")
    if lifecycle_row and activity_row and activity_row.get("lifecycle_code") != lifecycle_row.get("lifecycle_code"):
        blockers.append("LIFECYCLE_ACTIVITY_MISMATCH")

    lifecycle_code = str((lifecycle_row or {}).get("lifecycle_code") or "")
    activity_code = str((activity_row or {}).get("activity_code") or "")
    scenario_chain = str((activity_row or {}).get("chain_text") or "")

    customer_experience = _value(fields, "customer_experience")
    failure_mode = _value(fields, "failure_mode")
    requirement = _value(fields, "expected_quality_state", "quality_requirement_candidate")
    activity_label = str((activity_row or {}).get("label_zh") or activity_value)
    name_tail = customer_experience or failure_mode or requirement or _value(fields, "capability_gap")
    name = _join([activity_label, name_tail])[:160] or f"{canonical_itr}逆向质量场景候选"

    mapped = {
        "customer_perception": "customer_experience",
        "experience_requirement": "expected_quality_state",
        "quality_attribute": "quality_characteristic",
        "quality_subcharacteristic": "quality_element",
        "failure_mode": "failure_mode",
        "failure_mechanism": "failure_mechanism",
        "trigger_conditions": "trigger_condition",
        "preconditions": "preconditions",
        "operating_environment": "environment_constraints",
        "operating_condition": "operating_condition",
        "participating_systems": "related_objects",
        "system_scale": "scale_or_load",
        "business_impact": "business_impact",
        "recovery_method": "recovery_method",
    }
    candidate: dict[str, Any] = {
        "name": name,
        "lifecycle_code": lifecycle_code,
        "activity_code": activity_code,
        "scenario_chain": scenario_chain,
        "customer_perception": customer_experience,
        "primary_typical_problem_code": "",
        "secondary_typical_problem_codes": [],
        "primary_quality_concern_code": "",
        "secondary_quality_concern_codes": [],
        "primary_customer_experience_statement": "",
        "secondary_customer_experience_statements": [],
        "operating_environment": _value(fields, "environment_constraints"),
        "operating_condition": _value(fields, "operating_condition"),
        "operating_conditions": _value(fields, "operating_condition"),
        "duration_frequency": "",
        "disturbances": "",
        "extreme_conditions": "",
        "environment_condition_codes": [],
        "proposed_semantic_terms": [],
        "primary_experience_code": "",
        "secondary_experience_codes": [],
        "quality_in_use_codes": [],
        "primary_quality_characteristic_code": "",
        "secondary_quality_characteristic_codes": [],
        "quality_subcharacteristic_codes": [],
        "experience_requirement": requirement,
        "concern_points": _value(fields, "quality_risk", "capability_gap"),
        "quality_attribute": _value(fields, "quality_characteristic"),
        "quality_subcharacteristic": _value(fields, "quality_element"),
        "failure_mode": failure_mode,
        "failure_mechanism": _value(fields, "failure_mechanism"),
        "trigger_conditions": _value(fields, "trigger_condition"),
        "preconditions": _value(fields, "preconditions"),
        "participating_systems": _value(fields, "related_objects"),
        "system_scale": _value(fields, "scale_or_load"),
        "user_type": "",
        "affected_object": _value(fields, "related_objects"),
        "business_impact": _value(fields, "business_impact"),
        "recovery_method": _value(fields, "recovery_method"),
        "applicable_boundary": _value(fields, "environment_constraints"),
        "validation_direction": _join([
            _value(fields, "verification_method"),
            _value(fields, "test_requirement"),
        ])[:500],
        "measurement_suggestion": _join([
            _value(fields, "metric_candidate"),
            _value(fields, "metric_definition"),
            _value(fields, "target_candidate"),
        ])[:500],
        "evidence_issue_ids": [canonical_itr] if canonical_itr else [],
        "evidence_summary": (
            f"ReverseQualityResult {payload.get('result_version')} / "
            f"analysis={payload.get('analysis_id') or ''} / run={payload.get('run_id') or ''}"
        ),
        "confidence": 0.0,
        "confirmation_questions": [],
        "quality_classification_status": "PENDING_CONFIRMATION",
        "lifecycle_assessment": {},
        "lifecycle_reason": "来自 ReverseQualityResult V0.1 已完成的生命周期/业务活动判定",
    }

    confidences: list[float] = []
    field_evidence: dict[str, dict[str, Any]] = {}
    for candidate_field, reverse_field in mapped.items():
        item = _field(fields, reverse_field)
        if not item or not str(item.get("value") or "").strip():
            continue
        try:
            confidences.append(max(0.0, min(1.0, float(item.get("confidence") or 0))))
        except (TypeError, ValueError):
            pass
        field_evidence[candidate_field] = {
            "reverse_field": reverse_field,
            "source_type": str(item.get("source_type") or ""),
            "evidence_ids": list(item.get("evidence_ids") or []),
            "review_status": str(item.get("review_status") or ""),
        }
    candidate["confidence"] = min(confidences) if confidences else 0.0

    missing = payload.get("missing_information") or []
    if isinstance(missing, list):
        candidate["confirmation_questions"] = list(dict.fromkeys(
            str(item.get("question") or "").strip()
            for item in missing
            if isinstance(item, dict)
            and item.get("status") == "PENDING"
            and str(item.get("question") or "").strip()
        ))[:20]

    return ReverseQualityScenarioCandidate(
        adapter_version=ADAPTER_VERSION,
        source_result_version=str(payload.get("result_version") or ""),
        source_analysis_id=str(payload.get("analysis_id") or ""),
        source_run_id=str(payload.get("run_id") or ""),
        source_run_seq=int(payload.get("run_seq") or 0),
        canonical_itr=canonical_itr,
        product_code=product_code,
        taxonomy_version_id=taxonomy_version_id,
        ready=not blockers,
        blockers=list(dict.fromkeys(blockers)),
        candidate=candidate,
        field_evidence=field_evidence,
    )
