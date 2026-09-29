from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode


_DESCRIPTION_ALIASES = (
    "问题描述",
    "问题信息_问题描述",
    "问题现象",
    "问题信息_问题现象",
)
_STATUS_ALIASES = (
    "考核状态",
    "业务状态",
    "流程状态",
    "问题状态",
    "处理状态",
    "状态",
)
_RESULT_ALIASES = (
    "考核结果",
    "考核结论",
    "考核意见",
    "结论",
)
_OWNER_ALIASES = (
    "责任人",
    "责任人_姓名",
    "处理人",
    "当前处理人",
)
_DEPARTMENT_ALIASES = (
    "责任部门",
    "部门",
    "归属部门",
)
_SCORE_ALIASES = (
    "考核分",
    "考核得分",
    "扣分",
    "评分",
    "分值",
)


def _key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-—:：()（）\[\]【】×*]+", "", text)


def _pick(raw: dict[str, Any], aliases: tuple[str, ...]) -> str:
    normalized = {_key(key): value for key, value in raw.items()}
    for alias in aliases:
        value = normalized.get(_key(alias))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def build_software_assessment_rows(
    material_repository: Any,
    *,
    q: str = "",
    detail_prefix: str = "/issues",
    return_path: str = "/software-assessment",
) -> list[dict[str, Any]]:
    """Project imported software-operation facts into the restored assessment entry.

    This is deliberately a read-only Existing Capability mount.  It does not
    invent an assessment workflow, state machine, score, owner or transition
    when those fields are absent from the source material.
    """
    query = str(q or "").strip().lower()
    source_rows = material_repository.list_materials_with_issue_links("SW-OPS")
    rows: list[dict[str, Any]] = []

    for source in source_rows:
        raw = source.get("raw") if isinstance(source.get("raw"), dict) else {}
        row = {
            **source,
            "problem_description": _pick(raw, _DESCRIPTION_ALIASES),
            "assessment_status": _pick(raw, _STATUS_ALIASES),
            "assessment_result": _pick(raw, _RESULT_ALIASES),
            "assessment_owner": _pick(raw, _OWNER_ALIASES),
            "assessment_department": _pick(raw, _DEPARTMENT_ALIASES),
            "assessment_score": _pick(raw, _SCORE_ALIASES),
            "action_binding_status": "SOURCE_ACTION_BINDING_PENDING",
            "ownership": "SOURCE_OWNED_READ_ONLY",
        }

        searchable = " ".join(
            str(value or "")
            for value in (
                row.get("business_key"),
                row.get("linked_issue_id"),
                row.get("problem_description"),
                row.get("assessment_status"),
                row.get("assessment_result"),
                row.get("assessment_owner"),
                row.get("assessment_department"),
                row.get("assessment_score"),
                row.get("source_file"),
            )
        ).lower()
        if query and query not in searchable:
            continue

        if row.get("knowledge_id"):
            return_url = return_path + (("?" + urlencode({"q": q})) if q else "")
            row["detail_url"] = (
                f"{detail_prefix}/{row['knowledge_id']}?"
                + urlencode({"return_to": return_url})
                + "#overview"
            )
        else:
            row["detail_url"] = ""
        rows.append(row)
    return rows
