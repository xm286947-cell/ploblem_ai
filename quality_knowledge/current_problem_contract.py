"""Canonical Problem public contract for Overall R2 Wave 1.

This module defines identity/relation semantics only. It deliberately creates
no persistence and owns no source-domain workflow transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quality_knowledge.problem_refs import InvalidSourceProblemItrRef, SourceProblemItrRefV1


CONTRACT_VERSION = "canonical-problem/v1"
RELATION_CONTRACT_VERSION = "canonical-problem-relation/v1"
OWNER_DOMAIN = "EXISTING_PROBLEM"
WORKBENCH_COUNT = 4
COMMON_PROBLEM_VIEW_IS_WORKBENCH = False
RELATION_KEYS = ("ITR", "RESOLUTION", "SOFTWARE_ASSESSMENT", "MISSED_TEST")
READ_ONLY_ACCESS_MODE = "READ_ONLY_PROJECTION"
PERMISSION_AUTHORITY = "SOURCE_DOMAIN"
RELATION_POLICY = "EXPLICIT_OR_EXACT_UNIQUE_ONLY"
RETURN_CONTEXT_CONTRACT_VERSION = "overall-return-context/v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True, slots=True)
class CanonicalProblemIdentityV1:
    """Stable identity projected from the existing quality_issue master."""

    canonical_problem_id: str
    knowledge_id: str
    business_type: str
    business_issue_id: str
    source_problem_ref: SourceProblemItrRefV1
    issue_version_id: str = ""
    version_no: int | None = None

    @classmethod
    def from_issue(cls, issue: dict[str, Any] | None) -> "CanonicalProblemIdentityV1 | None":
        if not issue:
            return None
        knowledge_id = _text(issue.get("knowledge_id"))
        business_type = _text(issue.get("business_type")).upper()
        business_issue_id = _text(issue.get("business_issue_id"))
        if not knowledge_id or not business_type or not business_issue_id:
            return None
        try:
            source_ref = SourceProblemItrRefV1.from_input(business_issue_id)
        except InvalidSourceProblemItrRef:
            return None

        raw_version = issue.get("version_no")
        try:
            version_no = int(raw_version) if raw_version not in (None, "") else None
        except (TypeError, ValueError):
            version_no = None

        return cls(
            canonical_problem_id=f"{business_type}:{source_ref.canonical_itr}",
            knowledge_id=knowledge_id,
            business_type=business_type,
            business_issue_id=business_issue_id,
            source_problem_ref=source_ref,
            issue_version_id=_text(
                issue.get("issue_version_id") or issue.get("current_version_id")
            ),
            version_no=version_no,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "canonical_problem_id": self.canonical_problem_id,
            "owner_domain": OWNER_DOMAIN,
            "master_object_ref": {
                "domain": OWNER_DOMAIN,
                "object_type": "quality_issue",
                "object_id": self.knowledge_id,
            },
            "business_ref": {
                "business_type": self.business_type,
                "business_issue_id": self.business_issue_id,
            },
            "source_problem_ref": self.source_problem_ref.to_dict(),
            "version_ref": {
                "issue_version_id": self.issue_version_id,
                "version_no": self.version_no,
            },
            "common_problem_view_is_workbench": COMMON_PROBLEM_VIEW_IS_WORKBENCH,
            "workbench_count": WORKBENCH_COUNT,
        }


def build_relation_contract(
    identity: CanonicalProblemIdentityV1 | None,
    *,
    key: str,
    label: str,
    present: bool,
    count: int,
    status: str,
    href: str,
    evidence: list[str],
    source_domain: str,
    source_refs: list[dict[str, Any] | str] | None = None,
    no_relation_reason: str = "",
) -> dict[str, Any]:
    if key not in RELATION_KEYS:
        raise ValueError("CANONICAL_PROBLEM_RELATION_KEY_INVALID")
    clean_evidence = [str(item).strip() for item in evidence if str(item).strip()]
    clean_source_refs: list[dict[str, Any] | str] = []
    for item in source_refs if source_refs is not None else clean_evidence:
        if isinstance(item, dict):
            clean_source_refs.append(dict(item))
            continue
        text = str(item or "").strip()
        if text:
            clean_source_refs.append(text)
    return {
        "relation_contract_version": RELATION_CONTRACT_VERSION,
        "relation_policy": RELATION_POLICY,
        "key": key,
        "label": label,
        "present": bool(present),
        "count": max(0, int(count)),
        "status": status,
        "relation_status": "LINKED" if present else "NO_RELATION",
        "href": href if present else "",
        "evidence": clean_evidence,
        "source_refs": clean_source_refs if present else [],
        "no_relation_reason": "" if present else str(no_relation_reason or "RELATION_NOT_FOUND"),
        "canonical_problem_id": identity.canonical_problem_id if identity else "",
        "canonical_problem_ref": (
            identity.to_dict()["master_object_ref"] if identity else None
        ),
        "source_domain": source_domain,
        "access_mode": READ_ONLY_ACCESS_MODE,
        "permission_authority": PERMISSION_AUTHORITY,
        "return_context_contract": RETURN_CONTEXT_CONTRACT_VERSION,
        "version_ref": identity.to_dict()["version_ref"] if identity else {
            "issue_version_id": "",
            "version_no": None,
        },
    }


__all__ = [
    "COMMON_PROBLEM_VIEW_IS_WORKBENCH",
    "CONTRACT_VERSION",
    "CanonicalProblemIdentityV1",
    "OWNER_DOMAIN",
    "PERMISSION_AUTHORITY",
    "READ_ONLY_ACCESS_MODE",
    "RELATION_POLICY",
    "RETURN_CONTEXT_CONTRACT_VERSION",
    "RELATION_CONTRACT_VERSION",
    "RELATION_KEYS",
    "WORKBENCH_COUNT",
    "build_relation_contract",
]
