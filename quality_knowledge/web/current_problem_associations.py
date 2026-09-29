from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from quality_knowledge.current_problem_contract import (
    CONTRACT_VERSION,
    WORKBENCH_COUNT,
    CanonicalProblemIdentityV1,
    build_relation_contract,
)


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
    """Build the fail-closed W1 relation view around Existing Problem."""

    issue = service.get_issue(knowledge_id) if service is not None else None
    identity = CanonicalProblemIdentityV1.from_issue(issue)
    if not issue:
        return {
            "contract_version": CONTRACT_VERSION,
            "identity": None,
            "knowledge_id": knowledge_id,
            "canonical_problem_id": "",
            "relations": [],
            "related_count": 0,
            "workbench_count": WORKBENCH_COUNT,
        }

    materials = (
        material_repository.materials_for_issue(knowledge_id)
        if material_repository is not None and identity is not None
        else []
    )
    resolution = [row for row in materials if row.get("material_type") == "ITR_CS"]
    assessment = [
        row for row in materials if row.get("material_type") == "SOFTWARE_OPERATION"
    ]

    normalized = _safe_json(issue.get("normalized_json"))
    missed = bool(identity) and _is_missed_test_issue(normalized)

    routes = {
        "itr": "/p0/itr-recovery" if p0 else "/itr/recovery-workbench",
        "resolution": "/p0/itr-resolution" if p0 else "/itr/resolution-workbench",
        "assessment": "/p0/software-assessment" if p0 else "/software-assessment",
        "missed": "/p0/missed-test-analysis" if p0 else "/missed-test-analysis",
    }
    query = identity.source_problem_ref.canonical_itr if identity else ""

    def link(route: str, present: bool) -> str:
        if not present or not query:
            return ""
        return route + "?" + urlencode({"q": query})

    itr_present = identity is not None
    relations = [
        build_relation_contract(
            identity,
            key="ITR",
            label="ITR工作台",
            present=itr_present,
            count=1 if itr_present else 0,
            status="CANONICAL_PROBLEM_IDENTITY" if itr_present else "NO_RELATION",
            href=link(routes["itr"], itr_present),
            evidence=[identity.source_problem_ref.public_ref] if identity else [],
            source_domain="EXISTING_PROBLEM",
        ),
        build_relation_contract(
            identity,
            key="RESOLUTION",
            label="彻底解决工作台",
            present=bool(resolution),
            count=len(resolution),
            status="LINKED" if resolution else "NO_RELATION",
            href=link(routes["resolution"], bool(resolution)),
            evidence=[
                str(row.get("business_key") or row.get("source_file") or "")
                for row in resolution[:3]
            ],
            source_domain="ITR_RESOLUTION_SOURCE",
        ),
        build_relation_contract(
            identity,
            key="SOFTWARE_ASSESSMENT",
            label="软件考核工作台",
            present=bool(assessment),
            count=len(assessment),
            status="LINKED" if assessment else "NO_RELATION",
            href=link(routes["assessment"], bool(assessment)),
            evidence=[
                str(row.get("business_key") or row.get("source_file") or "")
                for row in assessment[:3]
            ],
            source_domain="SOFTWARE_ASSESSMENT_SOURCE",
        ),
        build_relation_contract(
            identity,
            key="MISSED_TEST",
            label="漏测分析",
            present=missed,
            count=1 if missed else 0,
            status="SOURCE_FACT_PRESENT" if missed else "NO_RELATION",
            href=link(routes["missed"], missed),
            evidence=["escape.is_escape"] if missed else [],
            source_domain="EXISTING_PROBLEM",
        ),
    ]

    return {
        "contract_version": CONTRACT_VERSION,
        "identity": identity.to_dict() if identity else None,
        "knowledge_id": knowledge_id,
        "canonical_problem_id": identity.canonical_problem_id if identity else "",
        "relations": relations,
        "related_count": sum(1 for relation in relations if relation["present"]),
        "workbench_count": WORKBENCH_COUNT,
    }
