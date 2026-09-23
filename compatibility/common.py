from __future__ import annotations

from copy import deepcopy
from typing import Any


def to_evidence(value: Any, *, reason: str = "Migrated from V2.2 legacy output", evidence_type: str = "UNKNOWN", confidence: float = 0.5) -> dict[str, Any]:
    if isinstance(value, dict) and "value" in value:
        result = deepcopy(value)
        result.setdefault("confidence", confidence)
        result.setdefault("evidence_type", evidence_type)
        result.setdefault("reason", reason)
        result.setdefault("source_refs", [])
        return result
    return {"value": value, "confidence": confidence, "evidence_type": evidence_type, "reason": reason, "source_refs": []}


def to_evidence_list(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    items = value if isinstance(value, list) else [value]
    return [to_evidence(item) for item in items]
