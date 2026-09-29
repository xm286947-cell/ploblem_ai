from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from quality_knowledge.problem_refs import InvalidSourceProblemItrRef, SourceProblemItrRefV1


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_missed_test_issue(normalized: dict[str, Any]) -> bool:
    escape = normalized.get("escape") if isinstance(normalized, dict) else {}
    if not isinstance(escape, dict):
        return False
    raw = escape.get("is_escape")
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().upper() in {"1", "TRUE", "YES", "Y", "是", "漏测"}


def build_current_problem_associations(
    service: Any,
    material_repository: Any,
    knowledge_id: str,
    *,
    p0: bool = True,
) -> dict[str, Any]:
    """Build one fail-closed relation view around the canonical Existing Problem.

    No relation is inferred from title, description, owner or other fuzzy fields.
    Resolution/software relations require an established or exact-unique canonical
    ITR material link. Missed-test requires an explicit Existing Problem source fact.
    """

    issue = service.get_issue(knowledge_id) if service is not None else None
    if not issue:
        return {
            "knowledge_id": knowledge_id,
            "canonical_problem_id": "",
            "relations": [],
            "related_count": 0,
        }

    business_issue_id = str(issue.get("business_issue_id") or "").strip()
    canonical_itr = ""
    try:
        canonical_itr = SourceProblemItrRefV1.from_input(business_issue_id).canonical_itr
    except InvalidSourceProblemItrRef:
        canonical_itr = ""

    materials = (
        material_repository.materials_for_issue(knowledge_id)
        if material_repository is not None
        else []
    )
    resolution = [row for row in materials if row.get("material_type") == "ITR_CS"]
    assessment = [
        row for row in materials if row.get("material_type") == "SOFTWARE_OPERATION"
    ]

    normalized = _safe_json(issue.get("normalized_json"))
    missed = _is_missed_test_issue(normalized)

    routes = {
        "itr": "/p0/itr-recovery" if p0 else "/itr/recovery-workbench",
        "resolution": "/p0/itr-resolution" if p0 else "/itr/resolution-workbench",
        "assessment": "/p0/software-assessment" if p0 else "/software-assessment",
        "missed": "/p0/missed-test-analysis" if p0 else "/missed-test-analysis",
    }
    query = canonical_itr or business_issue_id

    def link(route: str, present: bool) -> str:
        if not present or not query:
            return ""
        return route + "?" + urlencode({"q": query})

    relations = [
        {
            "key": "ITR",
            "label": "ITR工作台",
            "present": bool(canonical_itr),
            "count": 1 if canonical_itr else 0,
            "status": "CANONICAL_PROBLEM_IDENTITY" if canonical_itr else "NO_RELATION",
            "href": link(routes["itr"], bool(canonical_itr)),
            "evidence": [business_issue_id] if canonical_itr else [],
        },
        {
            "key": "RESOLUTION",
            "label": "彻底解决工作台",
            "present": bool(resolution),
            "count": len(resolution),
            "status": "LINKED" if resolution else "NO_RELATION",
            "href": link(routes["resolution"], bool(resolution)),
            "evidence": [
                str(row.get("business_key") or row.get("source_file") or "")
                for row in resolution[:3]
            ],
        },
        {
            "key": "SOFTWARE_ASSESSMENT",
            "label": "软件考核工作台",
            "present": bool(assessment),
            "count": len(assessment),
            "status": "LINKED" if assessment else "NO_RELATION",
            "href": link(routes["assessment"], bool(assessment)),
            "evidence": [
                str(row.get("business_key") or row.get("source_file") or "")
                for row in assessment[:3]
            ],
        },
        {
            "key": "MISSED_TEST",
            "label": "漏测分析",
            "present": missed,
            "count": 1 if missed else 0,
            "status": "SOURCE_FACT_PRESENT" if missed else "NO_RELATION",
            "href": link(routes["missed"], missed),
            "evidence": ["escape.is_escape"] if missed else [],
        },
    ]

    return {
        "knowledge_id": knowledge_id,
        "canonical_problem_id": canonical_itr or business_issue_id,
        "relations": relations,
        "related_count": sum(1 for relation in relations if relation["present"]),
    }
