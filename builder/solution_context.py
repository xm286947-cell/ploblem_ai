from __future__ import annotations

from typing import Any
import json

# M8.3 only needs information required to judge solution reuse.  Large retrieval
# documents, embeddings and raw report blocks are deliberately excluded here.
_QUERY_KEYS = {
    "problem", "problem_object", "phenomenon", "trigger_condition", "impact",
    "failure_mechanism", "trc", "mrc", "root_cause", "classification",
    "cause_level1", "cause_level2", "cause_level3", "cause_level4",
    "solution", "current_solution", "product", "domain", "version", "environment",
}
_CASE_KEYS = _QUERY_KEYS | {
    "corrective_action", "corrective_actions", "preventive_action", "preventive_actions",
    "verification", "verification_evidence", "closure", "closure_status", "effectiveness",
    "expected_effect", "solution_object", "solution_mechanism", "reusable_actions",
    "adaptation_required", "measure", "measures",
}
_SIMILARITY_KEYS = {
    "overall_score", "overall_level", "dimensions", "key_similarities",
    "key_differences", "evidence_gaps", "analysis_summary", "confidence",
}


def _keep_named(value: Any, allowed: set[str]) -> Any:
    """Recursively retain only semantically relevant named fields.

    Wrapper fields such as ``value/effective/original/normalized`` are retained when
    they are below a selected business field so Evidence DTOs remain readable to the
    model without shipping unrelated parts of the source object.
    """
    if isinstance(value, list):
        return [_keep_named(item, allowed) for item in value]
    if not isinstance(value, dict):
        return value

    out: dict[str, Any] = {}
    wrapper_keys = {"value", "effective", "original", "normalized", "inferred", "reason", "confidence", "evidence_type"}
    for key, item in value.items():
        if key in allowed:
            out[key] = _keep_all_wrappers(item)
            continue
        nested = _keep_named(item, allowed)
        if isinstance(nested, dict) and nested:
            out[key] = nested
        elif isinstance(nested, list) and nested:
            out[key] = nested
        elif key in wrapper_keys and nested not in (None, "", [], {}):
            out[key] = nested
    return out


def _keep_all_wrappers(value: Any) -> Any:
    if isinstance(value, list):
        return [_keep_all_wrappers(item) for item in value]
    if not isinstance(value, dict):
        return value
    # Once a relevant business field is reached keep its compact Evidence wrapper,
    # but still remove obvious large/raw payload keys.
    drop = {
        "embedding", "embedding_metadata", "retrieval_profile", "retrieval_document",
        "raw_evidence", "sections", "unclassified_blocks", "retrieval_text",
        "matched_report_path", "report_filename",
    }
    return {k: _keep_all_wrappers(v) for k, v in value.items() if k not in drop}


def _compact_query(context: dict[str, Any]) -> dict[str, Any]:
    query = context.get("query") or {}
    standard = query.get("standard_query") if isinstance(query, dict) else None
    return _keep_named(standard or query, _QUERY_KEYS)


def _compact_case(context: dict[str, Any]) -> dict[str, Any]:
    case = context.get("case") or {}
    if not isinstance(case, dict):
        return {}
    # Prefer normalized case objects. Raw evidence and retrieval text are excluded.
    source = {
        "standard_case": case.get("standard_case") or {},
        "enriched_case": case.get("enriched_case") or {},
    }
    return _keep_named(source, _CASE_KEYS)


def _compact_similarity(similarity: dict[str, Any] | None) -> dict[str, Any]:
    if not similarity:
        return {}
    analysis = similarity.get("analysis") or {}
    compact = _keep_named(analysis, _SIMILARITY_KEYS)
    compact["analysis_status"] = similarity.get("analysis_status") or "MISSING"
    return compact


def build_solution_payload(context: dict[str, Any], similarity: dict[str, Any] | None) -> dict[str, Any]:
    """Build the minimal M8.3 AI input contract from the full M8.1 context."""
    candidate = context.get("candidate") or {}
    return {
        "query_id": context.get("query_id"),
        "case_id": context.get("case_id"),
        "query": _compact_query(context),
        "candidate": {
            k: candidate.get(k)
            for k in ("case_id", "rank", "score", "retrieval_score", "retrieval_source")
            if isinstance(candidate, dict) and candidate.get(k) is not None
        },
        "historical_case": _compact_case(context),
        "similarity_analysis": _compact_similarity(similarity),
        "quality": {
            k: (context.get("quality") or {}).get(k)
            for k in ("status", "missing_sources", "quality_flags")
            if (context.get("quality") or {}).get(k) not in (None, "", [], {})
        },
    }


def payload_chars(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
