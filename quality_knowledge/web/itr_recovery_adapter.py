from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode


_FIELD_WORK_ALIASES = (
    "现场作业记录",
    "现场处理记录",
    "现场处置记录",
    "现场恢复记录",
    "现场处理",
    "现场处置",
    "现场恢复",
    "现场作业",
    "问题处理记录",
    "客户现场记录",
)

_WORKAROUND_ALIASES = (
    "临时措施",
    "临时解决方案",
    "现场措施",
    "规避措施",
    "workaround",
)

_STAGE_ALIASES = (
    "发生阶段",
    "问题发生阶段",
    "发现阶段",
    "实际发现阶段",
)

_SOURCE_URL_ALIASES = (
    "source_url",
    "source link",
    "source_link",
    "原始链接",
    "来源链接",
    "itr链接",
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


def _header_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-—:：()（）\[\]【】]+", "", text)


def _pick(raw: dict[str, Any], aliases: tuple[str, ...]) -> str:
    normalized = {_header_key(key): value for key, value in raw.items()}
    for alias in aliases:
        value = normalized.get(_header_key(alias))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _nested_text(normalized: dict[str, Any], section: str, *keys: str) -> str:
    payload = normalized.get(section)
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("value") or value.get("description")
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def build_itr_recovery_rows(
    service: Any,
    *,
    q: str = "",
    detail_prefix: str = "/issues",
    return_path: str = "/itr/recovery-workbench",
) -> list[dict[str, Any]]:
    """Read-only projection of authoritative issue/ITR source facts.

    The adapter never mutates source facts and does not invent a recovery state
    when no source field is present.  It only projects the current aligned issue
    view plus raw source evidence retained by the import boundary.
    """
    query = str(q or "").strip().lower()
    count = service.count_issues({})
    candidates = service.query_issues({}, max(count, 1))
    rows: list[dict[str, Any]] = []

    for candidate in candidates:
        knowledge_id = candidate.get("knowledge_id")
        if not knowledge_id:
            continue
        detail = service.get_issue_detail(knowledge_id)
        if not detail:
            continue
        issue = detail.get("issue") or {}
        raw_meta = detail.get("raw") or {}
        raw = _safe_json(raw_meta.get("raw_json"))
        normalized = _safe_json(issue.get("normalized_json"))

        field_work = _pick(raw, _FIELD_WORK_ALIASES)
        workaround = _pick(raw, _WORKAROUND_ALIASES)
        if not workaround:
            workaround = _nested_text(
                normalized,
                "solution",
                "original_solution",
                "corrective_action",
            )
        occurrence_stage = _pick(raw, _STAGE_ALIASES)
        source_url = _pick(raw, _SOURCE_URL_ALIASES)

        row = {
            **candidate,
            "field_work_record": field_work,
            "workaround": workaround,
            "occurrence_stage": occurrence_stage,
            "source_file": raw_meta.get("source_file") or "",
            "source_sheet": raw_meta.get("source_sheet") or "",
            "source_row": raw_meta.get("source_row"),
            "source_hash": raw_meta.get("source_hash") or "",
            "source_url": source_url,
            "source_fact_status": (
                "SOURCE_FACT_PRESENT"
                if field_work or workaround or occurrence_stage
                else "SOURCE_FACT_NO_RECOVERY_DETAIL"
            ),
            "ownership": "SOURCE_OWNED_READ_ONLY",
        }

        searchable = " ".join(
            str(row.get(key) or "")
            for key in (
                "business_issue_id",
                "title",
                "description",
                "product",
                "platform",
                "field_work_record",
                "workaround",
                "occurrence_stage",
                "source_file",
            )
        ).lower()
        if query and query not in searchable:
            continue

        return_url = return_path + (("?" + urlencode({"q": q})) if q else "")
        row["detail_url"] = (
            f"{detail_prefix}/{knowledge_id}?"
            + urlencode({"return_to": return_url})
            + "#trace"
        )
        rows.append(row)
    return rows
