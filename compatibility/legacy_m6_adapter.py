from __future__ import annotations

from copy import deepcopy
from typing import Any

from .common import to_evidence, to_evidence_list


def adapt_legacy_m6_case(payload: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(payload)
    result = {
        "dto_version": data.get("dto_version", "1.0.0"),
        "case_id": data.get("case_id") or data.get("problem_id") or data.get("itr_id") or "",
        "product": data.get("product", ""),
        "ipmt": data.get("ipmt", ""),
        "spdt": data.get("spdt", ""),
        "responsible_department_level2": data.get("responsible_department_level2", ""),
        "problem_description": data.get("problem_description", ""),
        "source_file": data.get("source_file"),
        "metadata": data.get("metadata", {}),
    }
    for field_name in ("feature",):
        if data.get(field_name) not in (None, ""):
            result[field_name] = to_evidence(data[field_name])
    for field_name in ("phenomena", "trigger_conditions", "failure_mechanisms", "root_causes", "keywords", "tags"):
        result[field_name] = to_evidence_list(data.get(field_name, []))
    return result
