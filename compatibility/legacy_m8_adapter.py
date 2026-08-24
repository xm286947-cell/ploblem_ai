from __future__ import annotations

from copy import deepcopy
from typing import Any


def adapt_legacy_repeat_decision(payload: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(payload)
    if "decision" in data and "query_id" in data:
        result = data
    else:
        metadata = data.get("metadata", {})
        decision = data.get("decision", data.get("repeat_decision", {}))
        if isinstance(decision, str):
            decision = {"decision": decision}
        result = {
            "query_id": metadata.get("query_id") or data.get("query_id") or "",
            "decision": decision.get("decision") or decision.get("status") or "INSUFFICIENT_EVIDENCE",
            "confidence": decision.get("confidence", data.get("confidence", 0.0)),
            "reasons": decision.get("reasons", data.get("reasons", [])),
            "supporting_evidence_ids": decision.get("supporting_evidence_ids", []),
            "conflicting_evidence_ids": decision.get("conflicting_evidence_ids", []),
            "candidate_case_ids": decision.get("candidate_case_ids", data.get("candidate_case_ids", [])),
            "metadata": {},
        }
    result.setdefault("dto_version", "1.0.0")
    result.setdefault("metadata", {})
    return result
