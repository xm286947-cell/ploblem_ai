from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from builder.validators import validate_json

STANDARD_BUILDER_VERSION = "M7.2-B1"
STANDARD_QUERY_SCHEMA_VERSION = "1.0"


class StandardQueryBuildError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _fact_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _fact_confidence(value: Any) -> float | None:
    if isinstance(value, dict) and isinstance(value.get("confidence"), (int, float)):
        return float(value["confidence"])
    return None


def _fact_evidence_type(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("evidence_type") or "")
    return ""


def _fact_reason(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("reason") or "")
    return ""


def _array_values(items: Any) -> list[Any]:
    if not isinstance(items, list):
        return []
    values: list[Any] = []
    for item in items:
        value = _fact_value(item)
        if _present(value):
            values.append(value)
    return values


def _scalar_field(
    original: Any = "",
    normalized: Any = "",
    inferred: Any = "",
    *,
    field_type: str = "fact",
    allow_ai_for_fact: bool = False,
) -> dict[str, Any]:
    inferred_value = _fact_value(inferred)
    confidence = _fact_confidence(inferred)

    if field_type == "analysis":
        candidates = [(inferred_value, "INFERRED"), (normalized, "NORMALIZED"), (original, "ORIGINAL")]
    elif field_type == "classification":
        # 原因分类允许 AI 对原始人工分类进行修正；只要 AI 返回非空值，
        # effective 必须使用 AI 修正结果，同时保留 original/normalized 供追溯。
        candidates = [(inferred_value, "INFERRED"), (normalized, "NORMALIZED"), (original, "ORIGINAL")]
    elif field_type == "mixed":
        candidates = [(normalized, "NORMALIZED"), (original, "ORIGINAL"), (inferred_value, "INFERRED")]
    else:
        candidates = [(normalized, "NORMALIZED"), (original, "ORIGINAL")]
        if allow_ai_for_fact:
            candidates.append((inferred_value, "INFERRED"))

    effective: Any = ""
    source = "EMPTY"
    for value, candidate_source in candidates:
        if _present(value):
            effective = deepcopy(value)
            source = candidate_source
            break

    result = {
        "original": deepcopy(original),
        "normalized": deepcopy(normalized),
        "inferred": deepcopy(inferred_value),
        "effective": effective,
        "effective_source": source,
    }
    if confidence is not None:
        result["confidence"] = confidence
    evidence_type = _fact_evidence_type(inferred)
    if evidence_type:
        result["evidence_type"] = evidence_type
    reason = _fact_reason(inferred)
    if reason:
        result["inference_reason"] = reason
    return result


def _list_field(
    original: Any = None,
    normalized: Any = None,
    inferred: Any = None,
    *,
    field_type: str = "analysis",
) -> dict[str, Any]:
    original_list = original if isinstance(original, list) else ([] if not _present(original) else [original])
    normalized_list = normalized if isinstance(normalized, list) else ([] if not _present(normalized) else [normalized])
    inferred_list = _array_values(inferred)

    if field_type == "analysis":
        candidates = [(inferred_list, "INFERRED"), (normalized_list, "NORMALIZED"), (original_list, "ORIGINAL")]
    else:
        candidates = [(normalized_list, "NORMALIZED"), (original_list, "ORIGINAL"), (inferred_list, "INFERRED")]
    effective: list[Any] = []
    source = "EMPTY"
    for value, candidate_source in candidates:
        if value:
            effective = deepcopy(value)
            source = candidate_source
            break
    return {
        "original": deepcopy(original_list),
        "normalized": deepcopy(normalized_list),
        "inferred": deepcopy(inferred_list),
        "effective": effective,
        "effective_source": source,
    }


class StandardQueryBuilder:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.schema_path = self.root / "schema/standard_query.schema.json"

    @staticmethod
    def _completeness(normalized: dict[str, Any], inferred: dict[str, Any]) -> dict[str, Any]:
        input_fields = [
            "query_id", "problem_description", "product", "ipmt", "spdt",
            "responsible_department_level2", "domain", "cause_description",
            "cause_level1", "cause_level2", "corrective_action", "preventive_action",
        ]
        input_present = sum(1 for key in input_fields if _present(normalized.get(key)))
        input_score = round(input_present / len(input_fields), 4)

        analysis_values = [
            _fact_value(inferred.get("problem_summary")),
            _fact_value(inferred.get("standard_problem_description")),
            _array_values(inferred.get("failure_objects")),
            _array_values(inferred.get("phenomena")),
            _array_values(inferred.get("trigger_conditions")),
            _array_values(inferred.get("impacts")),
            _fact_value(inferred.get("trc")),
            _fact_value(inferred.get("mrc")),
            _array_values(inferred.get("root_causes")),
            _array_values(inferred.get("failure_mechanisms")),
        ]
        analysis_present = sum(1 for value in analysis_values if _present(value))
        analysis_score = round(analysis_present / len(analysis_values), 4)
        return {
            "input_completeness": input_score,
            "analysis_completeness": analysis_score,
            "input_fields_present": input_present,
            "input_fields_total": len(input_fields),
            "analysis_fields_present": analysis_present,
            "analysis_fields_total": len(analysis_values),
        }

    def build(self, enriched_query: dict[str, Any], source_path: str = "") -> dict[str, Any]:
        original = deepcopy(enriched_query.get("original") or {})
        normalized = deepcopy(enriched_query.get("normalized") or {})
        inferred = deepcopy(enriched_query.get("inferred") or {})
        metadata = enriched_query.get("metadata") or {}
        ai_status = str(enriched_query.get("enrich_status") or "")

        classification_ai = inferred.get("classification") or {}
        solution_ai = inferred.get("solution") or {}

        quality_flags: list[str] = []
        if ai_status in {"SKIPPED", "AI_ENRICH_FAILED", "AI_OUTPUT_INVALID"}:
            quality_flags.append("AI_ENRICHMENT_UNAVAILABLE")
        if not _present(normalized.get("problem_description")):
            quality_flags.append("MISSING_PROBLEM_DESCRIPTION")
        if not _present(normalized.get("query_id")):
            quality_flags.append("MISSING_QUERY_ID")
        if inferred.get("information_gaps"):
            quality_flags.append("INFORMATION_GAPS_PRESENT")

        result: dict[str, Any] = {
            "metadata": {
                "query_id": str(normalized.get("query_id") or metadata.get("query_id") or ""),
                "source_enriched_query": source_path,
                "standard_builder_version": STANDARD_BUILDER_VERSION,
                "standard_query_schema_version": STANDARD_QUERY_SCHEMA_VERSION,
                "generated_at": _now(),
            },
            "organization": {
                "ipmt": _scalar_field(original.get("ipmt"), normalized.get("ipmt")),
                "spdt": _scalar_field(original.get("spdt"), normalized.get("spdt")),
                "responsible_department_level2": _scalar_field(original.get("responsible_department_level2"), normalized.get("responsible_department_level2")),
                "product": _scalar_field(original.get("product"), normalized.get("product")),
                "domain": _scalar_field(original.get("domain"), normalized.get("domain")),
            },
            "problem": {
                "query_id": _scalar_field(original.get("query_id"), normalized.get("query_id")),
                "itr_id": _scalar_field(original.get("itr_id"), normalized.get("itr_id")),
                "problem_description": _scalar_field(original.get("problem_description"), normalized.get("problem_description")),
                "problem_summary": _scalar_field(inferred=inferred.get("problem_summary"), field_type="analysis"),
                "standard_problem_description": _scalar_field(inferred=inferred.get("standard_problem_description"), field_type="analysis"),
                "failure_objects": _list_field(inferred=inferred.get("failure_objects")),
                "phenomena": _list_field(inferred=inferred.get("phenomena")),
                "trigger_conditions": _list_field(inferred=inferred.get("trigger_conditions")),
                "impacts": _list_field(inferred=inferred.get("impacts")),
                "operating_context": _list_field(inferred=inferred.get("operating_context")),
            },
            "analysis": {
                "cause_description": _scalar_field(original.get("cause_description"), normalized.get("cause_description"), field_type="mixed"),
                "trc": _scalar_field(original.get("trc", ""), normalized.get("trc", ""), inferred.get("trc"), field_type="mixed"),
                "mrc": _scalar_field(original.get("mrc", ""), normalized.get("mrc", ""), inferred.get("mrc"), field_type="mixed"),
                "root_causes": _list_field(inferred=inferred.get("root_causes")),
                "failure_mechanisms": _list_field(inferred=inferred.get("failure_mechanisms")),
                "contributing_factors": _list_field(inferred=inferred.get("contributing_factors")),
            },
            "classification": {
                "cause_level1": _scalar_field(original.get("cause_level1"), normalized.get("cause_level1"), classification_ai.get("cause_level1"), field_type="classification"),
                "cause_level2": _scalar_field(original.get("cause_level2"), normalized.get("cause_level2"), classification_ai.get("cause_level2"), field_type="classification"),
                "cause_level3": _scalar_field(original.get("cause_level3"), normalized.get("cause_level3"), classification_ai.get("cause_level3"), field_type="classification"),
                "cause_level4": _scalar_field(original.get("cause_level4"), normalized.get("cause_level4"), classification_ai.get("cause_level4"), field_type="classification"),
            },
            "solution": {
                "corrective_action": _scalar_field(original.get("corrective_action"), normalized.get("corrective_action"), field_type="mixed"),
                "preventive_action": _scalar_field(original.get("preventive_action"), normalized.get("preventive_action"), field_type="mixed"),
                "current_solution": _scalar_field(inferred=solution_ai.get("current_solution"), field_type="analysis"),
                "solution_object": _scalar_field(inferred=solution_ai.get("solution_object"), field_type="analysis"),
                "solution_mechanism": _scalar_field(inferred=solution_ai.get("solution_mechanism"), field_type="analysis"),
                "expected_effect": _scalar_field(inferred=solution_ai.get("expected_effect"), field_type="analysis"),
            },
            "knowledge": {
                "keywords": _array_values(inferred.get("keywords")),
                "tags": _array_values(inferred.get("tags")),
                "information_gaps": deepcopy(inferred.get("information_gaps") or []),
                "overall_confidence": float(_fact_value(inferred.get("overall_confidence")) or 0.0),
            },
            "extensions": deepcopy(enriched_query.get("extensions") or {}),
            "completeness": self._completeness(normalized, inferred),
            "quality_flags": quality_flags,
            "lineage": {
                "raw_query_version": "",
                "field_mapping_config_version": "",
                "normalizer_version": str(metadata.get("normalizer_version") or ""),
                "normalization_config_version": str(metadata.get("normalization_config_version") or ""),
                "ai_enricher_version": str(metadata.get("ai_enricher_version") or ""),
                "prompt_version": str(metadata.get("prompt_version") or ""),
                "model_provider": str(metadata.get("model_provider") or ""),
                "model_name": str(metadata.get("model_name") or ""),
                "standard_builder_version": STANDARD_BUILDER_VERSION,
                "standard_query_schema_version": STANDARD_QUERY_SCHEMA_VERSION,
            },
            "build_status": "PARTIAL_SUCCESS" if quality_flags else "SUCCESS",
        }

        errors = validate_json(result, self.schema_path)
        if errors:
            raise StandardQueryBuildError("; ".join(errors))
        return result
