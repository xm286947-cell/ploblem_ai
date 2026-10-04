"""Fail-closed adapter from frozen ScenarioSourceBundle V1 to mature RQ facts."""
from __future__ import annotations

import hashlib
import json
from typing import Any


CONTRACT_VERSION = "scenario-source-bundle/v1"
SOURCE_TYPES = ("SOFTWARE_ASSESSMENT", "RESOLUTION", "ITR", "MISSED_TEST")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _effective_analysis_refs(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [
        item for item in bundle.get("sources", [])
        if isinstance(item, dict)
        and item.get("source_type") == "MISSED_TEST"
        and item.get("evidence_kind") == "EFFECTIVE_ANALYSIS"
        and item.get("source_id")
    ]
    stages = bundle.get("effective_analysis") or {}
    by_run = {
        str(value.get("analysis_run_id")): stage
        for stage, value in stages.items()
        if isinstance(value, dict) and value.get("analysis_run_id")
    }
    seen: set[str] = set()
    for ref in refs:
        stage = str(ref.get("analysis_type") or ref.get("stage") or by_run.get(str(ref.get("source_id"))) or "").strip()
        if not stage:
            raise ValueError("INFORMATION_REQUIRED:MISSED_TEST_ANALYSIS_STAGE_UNKNOWN")
        if stage in seen:
            raise ValueError(f"INFORMATION_REQUIRED:MULTIPLE_EFFECTIVE_ANALYSIS_REFS:{stage}")
        seen.add(stage)
        if ref.get("binding_status") == "CONFLICT" or ref.get("status") == "CONFLICT":
            raise ValueError(f"INFORMATION_REQUIRED:EFFECTIVE_ANALYSIS_CONFLICT:{stage}")
    return refs


def validate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("SCENARIO_SOURCE_BUNDLE_CONTRACT_UNSUPPORTED")
    bundle_id = str(bundle.get("bundle_id") or "").strip()
    revision = str(bundle.get("bundle_revision") or "").strip()
    selected = bundle.get("selected_issue")
    if not bundle_id or not revision or not isinstance(selected, dict):
        raise ValueError("SCENARIO_SOURCE_BUNDLE_IDENTITY_INVALID")
    assessment_id = str(selected.get("software_assessment_record_id") or "").strip()
    if not assessment_id or bundle.get("source_status", {}).get("SOFTWARE_ASSESSMENT") != "PRESENT":
        raise ValueError("SOFTWARE_ASSESSMENT_SOURCE_REQUIRED")
    if not any(
        isinstance(ref, dict) and ref.get("source_type") == "SOFTWARE_ASSESSMENT"
        and ref.get("source_id") == assessment_id and ref.get("status") == "PRESENT"
        for ref in bundle.get("sources", [])
    ):
        raise ValueError("SOFTWARE_ASSESSMENT_SOURCE_REQUIRED")
    blockers = [
        item for item in bundle.get("missing_information", [])
        if isinstance(item, dict) and item.get("code") == "SOURCE_RELATION_CONFLICT"
    ]
    if blockers:
        source_types = ",".join(sorted({str(item.get("source_type") or "UNKNOWN") for item in blockers}))
        raise ValueError(f"INFORMATION_REQUIRED:SOURCE_RELATION_CONFLICT:{source_types}")
    _effective_analysis_refs(bundle)

    locator_index = {
        (str(ref.get("source_type") or ""), str(ref.get("source_id") or ""), str(ref.get("source_revision") or ""))
        for ref in bundle.get("sources", []) if isinstance(ref, dict) and ref.get("source_id")
    }
    for item in bundle.get("field_evidence", []):
        if not isinstance(item, dict):
            continue
        locator = (
            str(item.get("source_type") or ""),
            str(item.get("source_id") or ""),
            str(item.get("source_revision") or ""),
        )
        if not locator[1] or locator not in locator_index:
            raise ValueError("FROZEN_BUNDLE_EVIDENCE_LOCATOR_BROKEN")

    expected_id = "SSB-" + hashlib.sha256(assessment_id.encode("utf-8")).hexdigest()[:24]
    if bundle_id != expected_id:
        raise ValueError("SCENARIO_SOURCE_BUNDLE_ID_INVALID")
    refs_by_type = {source_type: [] for source_type in SOURCE_TYPES}
    for ref in bundle.get("sources", []):
        if isinstance(ref, dict) and ref.get("source_type") in refs_by_type and ref.get("source_id"):
            refs_by_type[ref["source_type"]].append({
                key: ref.get(key) for key in ("source_id", "source_revision", "version_no")
            })
    revision_material = {
        "contract_version": CONTRACT_VERSION,
        "bundle_id": bundle_id,
        "sources": refs_by_type,
        "analysis_revisions": bundle.get("analysis_revisions") or {},
        "trigger_source": str((bundle.get("trigger") or {}).get("trigger_source") or ""),
        "trigger_reason": str((bundle.get("trigger") or {}).get("trigger_reason") or ""),
    }
    expected_revision = hashlib.sha256(_json(revision_material).encode("utf-8")).hexdigest()
    if revision != expected_revision:
        raise ValueError("SCENARIO_SOURCE_BUNDLE_REVISION_INVALID")
    snapshot_metadata = bundle.get("snapshot_metadata") or {}
    snapshot_id = str(snapshot_metadata.get("snapshot_id") or "").strip()
    if not snapshot_id:
        raise ValueError("SOURCE_SNAPSHOT_ID_REQUIRED")
    return {"bundle_id": bundle_id, "bundle_revision": revision, "snapshot_id": snapshot_id,
            "selected_issue": selected, "source_refs": bundle.get("sources") or []}


def reverse_quality_facts_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    provenance = validate_bundle(bundle)
    bundle_facts = bundle.get("facts") or {}
    if not isinstance(bundle_facts, dict):
        raise ValueError("SCENARIO_SOURCE_BUNDLE_FACTS_INVALID")
    evidence: dict[str, dict[str, Any]] = {}
    evidence_by_target: dict[str, list[str]] = {}
    for item in bundle.get("field_evidence", []):
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        evidence[evidence_id] = {
            "id": evidence_id,
            "label": str(item.get("target_field") or "Bundle fact"),
            "value": str(item.get("excerpt_or_digest") or ""),
            "source": str(item.get("source_type") or ""),
            "source_type": str(item.get("source_type") or ""),
            "source_id": str(item.get("source_id") or ""),
            "source_revision": str(item.get("source_revision") or ""),
            "original_field": str(item.get("source_field") or ""),
            "target_field": str(item.get("target_field") or ""),
            "provenance": str(item.get("provenance") or ""),
        }
        evidence_by_target.setdefault(str(item.get("target_field") or ""), []).append(evidence_id)

    analysis = bundle.get("effective_analysis") or {}
    source_ref_by_stage = {}
    for ref in _effective_analysis_refs(bundle):
        stage = str(ref.get("analysis_type") or ref.get("stage") or "")
        if not stage:
            stage = next((name for name, row in analysis.items()
                          if isinstance(row, dict) and row.get("analysis_run_id") == ref.get("source_id")), "")
        if stage:
            source_ref_by_stage[stage] = ref
    for stage, record in analysis.items():
        if not isinstance(record, dict):
            continue
        result = record.get("effective_result") or record.get("result") or {}
        if not isinstance(result, dict):
            continue
        ref = source_ref_by_stage.get(stage, {})
        for key, value in result.items():
            if isinstance(value, dict):
                value = value.get("value")
            if value in (None, "", [], {}):
                continue
            human_confirmed = any(
                str(item.get("question_key") or "") == str(key)
                and str(item.get("status") or "").upper() in {"CONFIRMED", "CORRECTED"}
                and item.get("answer") not in (None, "", [], {})
                for item in record.get("human_confirmations", [])
                if isinstance(item, dict)
            )
            evidence_id = f"bundle.analysis.{stage}.{key}"
            evidence[evidence_id] = {
                "id": evidence_id, "label": f"有效分析/{stage}/{key}", "value": str(value),
                "source": "MISSED_TEST_ANALYSIS", "source_type": "MISSED_TEST",
                "source_id": str(ref.get("source_id") or record.get("analysis_run_id") or ""),
                "source_revision": str(ref.get("source_revision") or record.get("analysis_revision") or ""),
                "original_field": str(key), "target_field": str(key),
                "provenance": "HUMAN_CONFIRMED" if human_confirmed else "EFFECTIVE_ANALYSIS",
            }
            evidence_by_target.setdefault(str(key), []).append(evidence_id)

    fact_names = {
        "problem_description": "description", "root_cause": "root_cause", "corrective_actions": "solution",
        "verification_result": "verification_result", "occurrence_context": "phase",
        "product_model": "product_model", "product_code": "product_code", "customer": "customer",
        "industry": "customer_industry", "ipmt": "ipmt", "spdt": "spdt",
        "missed_test_cause": "missed_test_cause", "verification_gap": "verification_gap",
        "expected_detection_stage": "expected_detection_stage", "actual_detection_stage": "actual_detection_stage",
    }
    fact_evidence = {
        name: {"value": bundle_facts[name], "evidence_ids": evidence_by_target.get(name, [])}
        for name in fact_names if name in bundle_facts
    }
    if "root_cause" in fact_evidence and not fact_evidence["root_cause"]["evidence_ids"]:
        raise ValueError("BUNDLE_ROOT_CAUSE_EVIDENCE_REQUIRED")

    selected = provenance["selected_issue"]
    canonical = str(selected.get("business_issue_id") or selected.get("knowledge_id") or "").strip()
    if not canonical:
        raise ValueError("BUNDLE_CANONICAL_ITR_CONTEXT_REQUIRED")
    missing_analysis = bundle.get("source_status", {}).get("MISSED_TEST") == "MISSING"
    normalized = bundle_facts.get("normalized_facts") or {}
    return {
        "canonical_itr": canonical,
        "material_id": str(selected.get("software_assessment_record_id") or ""),
        "business_key": canonical,
        "product": str(bundle_facts.get("product_model") or selected.get("product_model") or ""),
        "problem_domain": "SOFTWARE",
        "source_status": "BUNDLE_FROZEN",
        "linked_issue_id": str(selected.get("knowledge_id") or ""),
        "warnings": ["没有有效漏测分析；不得生成已确认的漏测结论"] if missing_analysis else [],
        "evidence": evidence,
        "bundle_facts": fact_evidence,
        "missed_test_analysis": analysis,
        "missing_information": list(bundle.get("missing_information") or []),
        "normalized_facts": normalized,
        "bundle_provenance": {
            **provenance,
            "contract_version": bundle.get("contract_version"),
            "primary_source_type": bundle.get("primary_source_type"),
            "primary_source_id": bundle.get("primary_source_id"),
            "source_status": bundle.get("source_status") or {},
            "analysis_revisions": bundle.get("analysis_revisions") or {},
            "missing_information": list(bundle.get("missing_information") or []),
        },
    }


class ReverseQualityInputAdapter:
    """Named W2 input boundary for immutable Bundle V1 snapshots."""

    def adapt(self, bundle: dict[str, Any]) -> dict[str, Any]:
        return reverse_quality_facts_from_bundle(bundle)


__all__ = ["ReverseQualityInputAdapter", "reverse_quality_facts_from_bundle", "validate_bundle"]
