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

_CAUSE_ALIASES = (
    "问题原因",
    "问题原因定位",
    "问题原因定位（×开发填写×）",
    "根因",
    "根本原因",
    "原因分析",
    "原因分析_问题原因",
    "原因分析_根因",
)

_MEASURE_ALIASES = (
    "问题解决方案",
    "问题解决方案（×开发填写×）",
    "解决方案",
    "解决措施",
    "技术措施",
    "管理措施",
    "改进措施",
    "解决措施_技术措施",
    "解决措施_管理措施",
)

_VERIFICATION_ALIASES = (
    "验证结果",
    "验证",
    "测试验证结果",
    "验证信息_验证结果",
    "验证结果_验证结论",
)

_STATUS_ALIASES = (
    "业务状态",
    "单据状态",
    "流程状态",
    "状态",
    "问题状态",
    "处理状态",
)

_OWNER_ALIASES = (
    "责任人",
    "处理人",
    "当前处理人",
    "责任人_姓名",
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


def _pick_many(raw: dict[str, Any], aliases: tuple[str, ...]) -> list[str]:
    wanted = {_key(alias) for alias in aliases}
    seen: set[str] = set()
    values: list[str] = []
    for key, value in raw.items():
        if _key(key) not in wanted:
            continue
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            values.append(text)
    return values


def build_itr_resolution_rows(
    material_repository: Any,
    *,
    q: str = "",
    detail_prefix: str = "/issues",
    return_path: str = "/itr/resolution-workbench",
) -> list[dict[str, Any]]:
    """Project imported ITR-CS source facts without creating a workflow state.

    The source material remains authoritative.  Save/submit/transition actions
    are intentionally not synthesized; those actions stay pending until the
    original source-domain contract can be bound.
    """
    query = str(q or "").strip().lower()
    source_rows = material_repository.list_materials_with_issue_links("ITR-CS")
    rows: list[dict[str, Any]] = []

    for source in source_rows:
        raw = source.get("raw") if isinstance(source.get("raw"), dict) else {}
        measures = _pick_many(raw, _MEASURE_ALIASES)
        row = {
            **source,
            "problem_description": _pick(raw, _DESCRIPTION_ALIASES),
            "root_cause": _pick(raw, _CAUSE_ALIASES),
            "measures": measures,
            "verification_result": _pick(raw, _VERIFICATION_ALIASES),
            "business_status": _pick(raw, _STATUS_ALIASES),
            "source_owner": _pick(raw, _OWNER_ALIASES),
            "action_binding_status": "SOURCE_ACTION_BINDING_PENDING",
            "ownership": "SOURCE_OWNED_READ_ONLY",
        }

        searchable = " ".join(
            str(value or "")
            for value in (
                row.get("business_key"),
                row.get("linked_issue_id"),
                row.get("problem_description"),
                row.get("root_cause"),
                " ".join(measures),
                row.get("verification_result"),
                row.get("business_status"),
                row.get("source_owner"),
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
                + "#analysis"
            )
        else:
            row["detail_url"] = ""
        rows.append(row)
    return rows
