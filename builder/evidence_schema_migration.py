from __future__ import annotations

from copy import deepcopy
from typing import Any

EVIDENCE_TYPES = {"EXPLICIT", "SUMMARIZED", "INFERRED", "UNKNOWN"}

SCALAR_FACT_FIELDS = {
    "problem_summary",
    "standard_problem_description",
    "trc",
    "mrc",
    "overall_confidence",
}
ARRAY_FACT_FIELDS = {
    "failure_objects",
    "phenomena",
    "trigger_conditions",
    "impacts",
    "operating_context",
    "root_causes",
    "failure_mechanisms",
    "contributing_factors",
    "keywords",
    "tags",
}
CLASSIFICATION_FIELDS = {"cause_level1", "cause_level2", "cause_level3", "cause_level4"}
SOLUTION_FIELDS = {"current_solution", "solution_object", "solution_mechanism", "expected_effect"}

REQUIRED_INFERRED_FIELDS = (
    SCALAR_FACT_FIELDS
    | ARRAY_FACT_FIELDS
    | {"classification", "solution", "information_gaps"}
)


def missing_required_fields(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return sorted(REQUIRED_INFERRED_FIELDS)
    return sorted(field for field in REQUIRED_INFERRED_FIELDS if field not in data)


def _clamp_confidence(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _normalize_evidence_type(value: Any) -> str:
    text = str(value or "UNKNOWN").strip().upper()
    aliases = {
        "明确": "EXPLICIT",
        "显式": "EXPLICIT",
        "归纳": "SUMMARIZED",
        "总结": "SUMMARIZED",
        "推断": "INFERRED",
        "未知": "UNKNOWN",
        "不确定": "UNKNOWN",
    }
    text = aliases.get(text, text)
    return text if text in EVIDENCE_TYPES else "UNKNOWN"


def to_fact(value: Any, *, default_value: Any = "") -> dict[str, Any]:
    if isinstance(value, dict) and "value" in value:
        return {
            "value": deepcopy(value.get("value", default_value)),
            "evidence_type": _normalize_evidence_type(value.get("evidence_type")),
            "confidence": _clamp_confidence(value.get("confidence")),
            "reason": str(value.get("reason") or "").strip(),
        }
    if value is None:
        value = default_value
    empty = value == "" or value == [] or value == {}
    return {
        "value": deepcopy(value),
        "evidence_type": "UNKNOWN" if empty else "INFERRED",
        "confidence": 0.0 if empty else 0.5,
        "reason": "",
    }


def to_fact_array(value: Any) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    items = value if isinstance(value, list) else [value]
    result: list[dict[str, Any]] = []
    for item in items:
        fact = to_fact(item)
        if fact["value"] not in (None, "", [], {}):
            result.append(fact)
    return result


def migrate_inferred_evidence(data: Any) -> tuple[dict[str, Any], bool]:
    """Normalize legacy/mixed AI output into the V2.3 evidence contract.

    This function fixes shape drift only. It does not invent business content.
    """
    source = deepcopy(data) if isinstance(data, dict) else {}
    result = deepcopy(source)

    for field in SCALAR_FACT_FIELDS:
        default = 0.0 if field == "overall_confidence" else ""
        result[field] = to_fact(source.get(field), default_value=default)

    for field in ARRAY_FACT_FIELDS:
        result[field] = to_fact_array(source.get(field))

    classification = source.get("classification") if isinstance(source.get("classification"), dict) else {}
    result["classification"] = {
        field: to_fact(classification.get(field)) for field in CLASSIFICATION_FIELDS
    }

    solution = source.get("solution") if isinstance(source.get("solution"), dict) else {}
    result["solution"] = {field: to_fact(solution.get(field)) for field in SOLUTION_FIELDS}

    gaps = source.get("information_gaps")
    result["information_gaps"] = gaps if isinstance(gaps, list) else []

    return result, result != source
