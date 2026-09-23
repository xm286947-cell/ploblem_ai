from __future__ import annotations

from copy import deepcopy
from typing import Any

from .common import to_evidence, to_evidence_list


def adapt_legacy_m7_query(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept either the new DTO shape or common V2.2 enriched-query shapes."""
    data = deepcopy(payload)
    if "query_id" in data and "problem_description" in data:
        result = data
    else:
        metadata = data.get("metadata", {})
        original = data.get("original", {})
        normalized = data.get("normalized", {})
        inferred = data.get("inferred", {})
        result = {
            "query_id": metadata.get("query_id") or original.get("query_id") or normalized.get("query_id") or "",
            "product": original.get("product") or normalized.get("product") or "",
            "ipmt": original.get("ipmt") or normalized.get("ipmt") or "",
            "spdt": original.get("spdt") or normalized.get("spdt") or "",
            "responsible_department_level2": original.get("responsible_department_level2") or normalized.get("responsible_department_level2") or "",
            "problem_description": original.get("problem_description") or normalized.get("problem_description") or "",
            "source_file": metadata.get("source_normalized_query"),
            "problem_summary": inferred.get("problem_summary"),
            "standard_problem_description": inferred.get("standard_problem_description"),
            "feature": inferred.get("feature"),
            "phenomena": inferred.get("phenomena", []),
            "trigger_conditions": inferred.get("trigger_conditions", []),
            "failure_mechanisms": inferred.get("failure_mechanisms", []),
            "possible_root_causes": inferred.get("root_causes", []),
            "keywords": inferred.get("keywords", []),
            "tags": inferred.get("tags", []),
            "overall_confidence": inferred.get("overall_confidence", 0.0),
        }

    for field_name in ("problem_summary", "standard_problem_description", "feature"):
        if result.get(field_name) not in (None, ""):
            result[field_name] = to_evidence(result[field_name])
    for field_name in ("phenomena", "trigger_conditions", "failure_mechanisms", "possible_root_causes", "keywords", "tags"):
        result[field_name] = to_evidence_list(result.get(field_name, []))
    if result.get("overall_confidence") is not None:
        result["overall_confidence"] = to_evidence(result.get("overall_confidence", 0.0))
    result.setdefault("dto_version", "1.0.0")
    result.setdefault("metadata", {})
    return result
