from __future__ import annotations

from typing import Any, Dict, List
import json
import re

from builder.validators import validate_json
from builder.json_response import parse_json_object


class AIResponseParseError(ValueError):
    pass


ARRAY_FIELDS = [
    "phenomenon","failure_object","trigger_condition","failure_mechanism",
    "contributing_factors","reusable_actions","phenomenon_tags",
    "failure_object_tags","trigger_tags","failure_mechanism_tags",
    "cause_tags","solution_tags","keywords",
]

STRING_FIELDS = [
    "standard_description","problem_summary","trc_occurrence_standard",
    "trc_escape_standard","mrc_occurrence_standard","mrc_escape_standard",
    "case_summary","normalized_problem","retrieval_text",
]


def _extract_json_object(text: str) -> dict:
    value, _ = parse_json_object(text, allow_repair=True)
    return value


def _strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = [value]
    result: List[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_response(data: dict) -> dict:
    result: Dict[str, Any] = {}
    for field in STRING_FIELDS:
        result[field] = str(data.get(field, "") or "").strip()
    for field in ARRAY_FIELDS:
        result[field] = _strings(data.get(field))

    cls = data.get("ai_classification") or {}
    try:
        confidence = float(cls.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    result["ai_classification"] = {
        "cause_level1": str(cls.get("cause_level1", "") or "").strip(),
        "cause_level2": str(cls.get("cause_level2", "") or "").strip(),
        "reason": str(cls.get("reason", "") or "").strip(),
        "confidence": confidence,
    }
    return result


def parse_and_validate(text: str, schema_path: str) -> dict:
    data = normalize_response(_extract_json_object(text))
    errors = validate_json(data, schema_path)
    if errors:
        raise AIResponseParseError("AI响应Schema校验失败: " + " | ".join(errors))
    return data
