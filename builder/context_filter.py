from __future__ import annotations

from typing import Any, Mapping, Sequence

ALIASES = {
    "ipmt": ("ipmt", "IPMT"),
    "spdt": ("spdt", "SPDT"),
    "department": ("responsible_department_level2", "责任部门（二级）", "责任部门(二级)", "责任部门"),
}


def _display(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("effective", "value", "normalized", "inferred", "original"):
            if value.get(key) not in (None, "", [], {}):
                return _display(value.get(key))
        return ""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "、".join(x for item in value if (x := _display(item)))
    return "" if value is None else str(value).strip()


def _find(node: Any, aliases: tuple[str, ...]) -> str:
    if isinstance(node, Mapping):
        for alias in aliases:
            if alias in node:
                value = _display(node.get(alias))
                if value:
                    return value
        for value in node.values():
            found = _find(value, aliases)
            if found:
                return found
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        for value in node:
            found = _find(value, aliases)
            if found:
                return found
    return ""


def evaluate_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    context = context or {}
    query = context.get("query") or {}
    case = context.get("case") or {}
    rows: list[dict[str, str]] = []
    matches = 0
    comparable = 0
    labels = {"ipmt": "IPMT", "spdt": "SPDT", "department": "责任部门"}
    for key, aliases in ALIASES.items():
        current = _find(query, aliases)
        historical = _find(case, aliases)
        if current and historical:
            comparable += 1
            status = "一致" if current.strip().lower() == historical.strip().lower() else "不同"
            if status == "一致":
                matches += 1
        else:
            status = "信息不足"
        rows.append({"field": labels[key], "current": current, "historical": historical, "status": status})

    if comparable == 0:
        level, conclusion = "未知", "组织信息不足，需人工判断适用性"
    elif matches == comparable:
        level, conclusion = "高", "组织上下文一致，可优先参考"
    elif matches >= 1:
        level, conclusion = "中", "组织上下文部分一致，复用前需适配"
    else:
        level, conclusion = "低", "组织上下文不同，仅建议作为方法参考"
    return {"level": level, "conclusion": conclusion, "details": rows}
