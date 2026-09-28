from __future__ import annotations

from urllib.parse import urlencode
from typing import Any


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("value") or value.get("description") or "")
    return str(value)


def is_missed_test_issue(normalized: dict[str, Any]) -> bool:
    escape = normalized.get("escape") if isinstance(normalized, dict) else {}
    if not isinstance(escape, dict):
        return False
    raw = escape.get("is_escape")
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().upper() in {"1", "TRUE", "YES", "Y", "是", "漏测"}


def build_missed_test_rows(
    service: Any,
    *,
    q: str = "",
    analysis_status: str = "",
    detail_prefix: str = "/issues",
    return_path: str = "/missed-test-analysis",
    detail_anchor: str = "causes",
) -> list[dict[str, Any]]:
    """Project existing issue + escape-analysis facts into a missed-test workbench.

    This adapter is read-only: it creates no missed-test master data and does not
    infer a missed-test issue unless the existing normalized source fact says so.
    """
    query = str(q or "").strip()
    status_filter = str(analysis_status or "").strip().upper()
    issue_count = service.count_issues({})
    candidates = service.query_issues({}, max(issue_count, 1))
    rows: list[dict[str, Any]] = []

    for candidate in candidates:
        knowledge_id = candidate.get("knowledge_id")
        if not knowledge_id:
            continue
        issue = service.get_issue(knowledge_id)
        if not issue:
            continue
        normalized = _safe_json(issue.get("normalized_json"))
        if not is_missed_test_issue(normalized):
            continue

        escape_analysis = service.get_latest_analysis(knowledge_id, "escape")
        result = (escape_analysis or {}).get("result") or {}
        row = {
            **candidate,
            "escape_analysis_status": (escape_analysis or {}).get("status") or "NOT_ANALYZED",
            "escape_cause_summary": _text(result.get("escape_cause_summary")),
            "verification_gap": _text(result.get("verification_gap")),
            "expected_detection_stage": result.get("expected_detection_stage") or "",
            "actual_detection_stage": result.get("actual_detection_stage") or "",
        }
        searchable = " ".join(
            str(row.get(key) or "")
            for key in (
                "business_issue_id",
                "title",
                "description",
                "product",
                "platform",
                "escape_cause_summary",
                "verification_gap",
            )
        ).lower()
        if query and query.lower() not in searchable:
            continue
        if status_filter and row["escape_analysis_status"].upper() != status_filter:
            continue

        return_params: list[tuple[str, str]] = []
        if query:
            return_params.append(("q", query))
        if status_filter:
            return_params.append(("analysis_status", status_filter))
        return_url = return_path + (("?" + urlencode(return_params)) if return_params else "")
        row["detail_url"] = (
            f"{detail_prefix}/{knowledge_id}?"
            + urlencode({"return_to": return_url})
            + "#" + detail_anchor
        )
        rows.append(row)
    return rows
