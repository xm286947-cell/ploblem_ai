from __future__ import annotations

from copy import deepcopy
from typing import Any


_MISSED_TEST_KEYS = {
    "关联漏测问题",
    "关联漏测问题单",
    "漏测问题单号",
    "漏测问题编号",
    "漏测问题ID",
    "missed_test_ref",
    "missed_test_id",
    "related_missed_test",
    "related_missed_test_ref",
}

_BOOLEANISH = {"是", "否", "yes", "no", "true", "false", "1", "0"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _find_recursive(mapping: dict[str, Any], keys: set[str]) -> Any:
    for key, value in mapping.items():
        if str(key) in keys and value not in (None, "", [], {}):
            return value
    for value in mapping.values():
        if isinstance(value, dict):
            found = _find_recursive(value, keys)
            if found not in (None, "", [], {}):
                return found
    return None


class P0ITRSubjectSource:
    """Adapter from the existing issue/ITR workbench into Repeat Risk.

    The host repository remains the source of truth.  This adapter only reads
    the current issue and projects a Repeat subject; it does not create another
    ITR master-data store.
    """

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def _issue_by_ref(self, ref: str) -> dict[str, Any] | None:
        issue = self.repository.get_issue(ref)
        if issue:
            return issue
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT knowledge_id FROM quality_issue WHERE business_issue_id=?",
                (ref,),
            ).fetchone()
        return self.repository.get_issue(row["knowledge_id"]) if row else None

    def get_itr(self, itr_ref: str) -> dict[str, Any] | None:
        issue = self._issue_by_ref(itr_ref)
        if not issue:
            return None

        normalized = _mapping(issue.get("normalized_snapshot"))
        raw = _mapping(issue.get("raw_json"))
        fact = _mapping(
            normalized.get("ISSUE_FACT")
            or normalized.get("issue_fact")
            or normalized.get("fact")
        )
        product_context = _mapping(
            normalized.get("PRODUCT_CONTEXT")
            or normalized.get("product_context")
        )
        occurrence = _mapping(
            normalized.get("OCCURRENCE")
            or normalized.get("occurrence")
        )
        recurrence = _mapping(
            normalized.get("RECURRENCE")
            or normalized.get("recurrence")
        )

        analysis = self.repository.get_latest_analysis_set(issue["knowledge_id"])
        effective = (
            self.repository.get_effective_analysis(analysis["analysis_set_id"])
            if analysis
            else None
        )
        effective_values = _mapping((effective or {}).get("values"))

        existing_context = {
            "root_cause": _first(
                fact,
                "root_cause",
                "cause_description",
                "occurrence_cause",
            )
            or _first(occurrence, "root_cause", "cause", "reason"),
            "technical_cause": _first(
                fact,
                "technical_cause",
                "技术原因",
            ),
            "management_cause": _first(
                fact,
                "management_cause",
                "管理原因",
            ),
            "solution": _first(
                fact,
                "solution",
                "corrective_action",
                "measure",
            ),
            "action": _first(
                recurrence,
                "action",
                "recommended_action",
                "preventive_action",
            ),
            "domain": _first(fact, "issue_domain", "domain"),
            "effective_analysis": deepcopy(effective_values),
        }
        existing_context = {
            key: value
            for key, value in existing_context.items()
            if value not in (None, "", [], {})
        }

        return {
            "itr_id": issue.get("business_issue_id") or itr_ref,
            "problem_description": _first(
                fact,
                "description",
                "problem_description",
                "title",
            )
            or _first(raw, "问题描述", "故障现象", "现象"),
            "product": _first(
                fact,
                "product",
                "product_name",
            )
            or _first(product_context, "product", "product_name")
            or issue.get("product_code"),
            "version": _first(
                fact,
                "version",
                "product_version",
                "software_version",
            )
            or _first(
                product_context,
                "version",
                "product_version",
                "software_version",
            ),
            "scene": _first(
                fact,
                "scene",
                "scenario",
                "lifecycle_scene",
                "lifecycle_phase",
            )
            or _first(
                product_context,
                "scene",
                "scenario",
                "lifecycle_scene",
            ),
            "existing_context": existing_context,
            "itr_version": issue.get("issue_version_id") or issue.get("version_no"),
        }

    def get_related_missed_test_ref(self, itr_ref: str) -> str | None:
        issue = self._issue_by_ref(itr_ref)
        if not issue:
            return None
        for source in (
            _mapping(issue.get("normalized_snapshot")),
            _mapping(issue.get("raw_json")),
        ):
            value = _find_recursive(source, _MISSED_TEST_KEYS)
            text = _text(value)
            if text and text.lower() not in _BOOLEANISH:
                return text
        return None

    def get_missed_test(self, missed_test_ref: str) -> dict[str, Any] | None:
        issue = self._issue_by_ref(missed_test_ref)
        if not issue:
            return None
        normalized = _mapping(issue.get("normalized_snapshot"))
        fact = _mapping(
            normalized.get("ISSUE_FACT")
            or normalized.get("issue_fact")
            or normalized.get("fact")
        )
        raw = _mapping(issue.get("raw_json"))
        return {
            "missed_test_id": issue.get("business_issue_id") or missed_test_ref,
            "description": _first(
                fact,
                "description",
                "problem_description",
                "title",
            )
            or _first(raw, "问题描述", "故障现象", "现象"),
            "test_gap": _first(
                fact,
                "test_gap",
                "verification_gap",
                "漏测原因",
            )
            or _first(raw, "漏测原因", "测试缺口"),
            "root_cause": _first(
                fact,
                "root_cause",
                "cause_description",
            ),
            "version": issue.get("issue_version_id") or issue.get("version_no"),
        }
