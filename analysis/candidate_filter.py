from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


_DEPARTMENT_ALIASES = {
    "ipmt": ("ipmt", "IPMT", "所属IPMT", "责任IPMT"),
    "spdt": ("spdt", "SPDT", "所属SPDT", "责任SPDT", "部门", "责任部门"),
}


@dataclass(frozen=True, slots=True)
class FilterDecision:
    accepted: bool
    mode: str
    query_department: str = ""
    case_department: str = ""
    reason: str = ""


class CandidateFilter:
    """Filter candidates by organization before repeat analysis.

    strict: reject only when both sides have a department and they differ.
    preferred/ignore: retain the candidate. Missing department data never causes
    a hard rejection because source fields may be incomplete or incorrectly filled.
    """

    VALID_MODES = {"strict", "preferred", "ignore"}

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        department_cfg = cfg.get("department") or {}
        if isinstance(department_cfg, str):
            department_cfg = {"mode": department_cfg}
        self.enabled = bool(department_cfg.get("enabled", True))
        self.mode = str(department_cfg.get("mode") or "strict").strip().lower()
        if self.mode not in self.VALID_MODES:
            raise ValueError(f"非法部门过滤模式: {self.mode}")

    def evaluate(self, context: Mapping[str, Any]) -> FilterDecision:
        query = context.get("query") or {}
        historical = context.get("case") or {}
        query_department = self._department(query)
        case_department = self._department(historical)

        if not self.enabled or self.mode == "ignore":
            return FilterDecision(True, self.mode, query_department, case_department, "部门过滤未启用")
        if not query_department or not case_department:
            return FilterDecision(True, self.mode, query_department, case_department, "部门信息缺失，保留候选供人工判断")
        if self._normalize(query_department) == self._normalize(case_department):
            return FilterDecision(True, self.mode, query_department, case_department, "部门一致")
        if self.mode == "strict":
            return FilterDecision(False, self.mode, query_department, case_department, "部门不一致，按strict模式过滤")
        return FilterDecision(True, self.mode, query_department, case_department, "部门不一致，按preferred模式保留")

    @classmethod
    def _department(cls, value: Any) -> str:
        # Prefer SPDT because it is the direct department boundary; fall back to IPMT.
        for group in ("spdt", "ipmt"):
            found = cls._find_alias(value, _DEPARTMENT_ALIASES[group])
            if found:
                return found
        return ""

    @classmethod
    def _find_alias(cls, value: Any, aliases: tuple[str, ...]) -> str:
        alias_norm = {cls._normalize_key(item) for item in aliases}
        if isinstance(value, Mapping):
            for key, item in value.items():
                if cls._normalize_key(key) in alias_norm and item not in (None, "", [], {}):
                    return cls._string(item)
            for item in value.values():
                found = cls._find_alias(item, aliases)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = cls._find_alias(item, aliases)
                if found:
                    return found
        return ""

    @staticmethod
    def _string(value: Any) -> str:
        if isinstance(value, list):
            return "/".join(str(item).strip() for item in value if str(item).strip())
        return str(value).strip()

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(str(value).lower().split()).replace("-", "").replace("_", "")

    @staticmethod
    def _normalize_key(value: Any) -> str:
        return CandidateFilter._normalize(str(value))
