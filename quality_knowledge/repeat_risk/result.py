from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .repository import RepeatQueryTraceRepository
from .search import (
    SEARCH_INCOMPLETE,
    SEARCH_NO_CANDIDATES,
    SEARCH_SUCCESS,
    SEARCH_UNAVAILABLE,
)

RESULT_CONTRACT_VERSION = "repeat-result/v1"

RESULT_READY_FOR_REVIEW = "READY_FOR_REVIEW"
RESULT_NO_CANDIDATES = "NO_CANDIDATES"
RESULT_SEARCH_UNAVAILABLE = "SEARCH_UNAVAILABLE"
RESULT_INCOMPLETE = "INCOMPLETE"

HUMAN_DECISIONS = {
    "REPEAT",
    "SIMILAR",
    "NOT_REPEAT",
    "INSUFFICIENT_EVIDENCE",
}

_MATCHED_FIELD_LABELS = {
    "problem": "问题现象",
    "cause": "原因",
    "solution": "措施",
    "product": "产品",
    "domain": "业务域",
    "classification": "原因分类",
    "organization": "组织上下文",
}


class RepeatResultContractError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _unique_text(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    for item in values:
        text = _text(item)
        if text and text not in result:
            result.append(text)
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RepeatResultService:
    """Build a reviewable Repeat Risk result from REPEAT-SEARCH-001 output.

    The service never auto-selects REPEAT / SIMILAR / NOT_REPEAT.  The system
    organizes evidence and explainability; the final decision remains human.
    """

    def __init__(self, repository: RepeatQueryTraceRepository) -> None:
        self.repository = repository

    def build(self, search_result: dict[str, Any]) -> dict[str, Any]:
        query_id = _text(search_result.get("query_id"))
        if not query_id:
            raise RepeatResultContractError("QUERY_ID_REQUIRED")

        trace = self.repository.get(query_id)
        if not trace:
            raise RepeatResultContractError("REPEAT_QUERY_NOT_FOUND")

        existing = self.repository.get_result(query_id)
        if existing:
            return existing

        search_status = _text(search_result.get("status"))
        result_status = self._result_status(search_status)
        raw_candidates = search_result.get("candidates")
        if not isinstance(raw_candidates, list):
            raise RepeatResultContractError("REPEAT_SEARCH_RESULT_INVALID")

        candidates = [self._candidate(item) for item in raw_candidates]
        warnings: list[dict[str, str]] = []

        for candidate in candidates:
            if candidate["explanation_status"] != "EXPLAINED":
                warnings.append({
                    "code": "RELEVANCE_EXPLANATION_INSUFFICIENT",
                    "message": f"{candidate['case_id']}: 检索命中，但当前结果缺少足够的可解释关联依据。",
                })
            if candidate["detail_status"] != SEARCH_SUCCESS:
                warnings.append({
                    "code": "CANDIDATE_DETAIL_INCOMPLETE",
                    "message": f"{candidate['case_id']}: 历史案例详情不完整。",
                })

        if result_status == RESULT_SEARCH_UNAVAILABLE:
            warnings.append({
                "code": "SEARCH_UNAVAILABLE",
                "message": "历史案例检索当前不可用；该状态不能解释为 NOT_REPEAT。",
            })
        elif result_status == RESULT_INCOMPLETE:
            warnings.append({
                "code": "SEARCH_INCOMPLETE",
                "message": "历史案例检索结果不完整；请补充证据或恢复检索能力后再判断。",
            })
        elif result_status == RESULT_NO_CANDIDATES:
            warnings.append({
                "code": "NO_CANDIDATES",
                "message": "本次查询未召回历史候选；这不等价于 NOT_REPEAT。",
            })

        result = {
            "contract_version": RESULT_CONTRACT_VERSION,
            "query_id": query_id,
            "subject_ref": trace["subject_ref"],
            "correlation_id": trace["correlation_id"],
            "query_snapshot": {
                "itr_version": trace.get("itr_version"),
                "include_missed_test": bool(trace.get("include_missed_test")),
                "missed_test_ref": trace.get("missed_test_ref"),
                "algorithm_version": trace.get("algorithm_version"),
            },
            "search_status": search_status,
            "result_status": result_status,
            "search_error": search_result.get("error_code"),
            "candidate_count": len(candidates),
            "candidates": candidates,
            "warnings": warnings,
            "human_decision": {
                "decision": "PENDING",
                "decided_by": None,
                "reason": None,
                "decided_at": None,
            },
            "generated_at": _now(),
        }
        return self.repository.save_result(result)

    def get(self, query_id: str) -> dict[str, Any]:
        result = self.repository.get_result(query_id)
        if not result:
            raise RepeatResultContractError("REPEAT_RESULT_NOT_FOUND")
        return result

    def decide(
        self,
        query_id: str,
        decision: str,
        *,
        decided_by: str,
        reason: str = "",
    ) -> dict[str, Any]:
        normalized = _text(decision).upper()
        if normalized not in HUMAN_DECISIONS:
            raise RepeatResultContractError("INVALID_REPEAT_DECISION")
        if not _text(decided_by):
            raise RepeatResultContractError("DECIDED_BY_REQUIRED")
        if not self.repository.get_result(query_id):
            raise RepeatResultContractError("REPEAT_RESULT_NOT_FOUND")
        try:
            return self.repository.save_human_decision(
                query_id,
                normalized,
                decided_by=decided_by,
                reason=reason,
                decided_at=_now(),
            )
        except ValueError as exc:
            raise RepeatResultContractError(str(exc)) from exc
        except KeyError as exc:
            raise RepeatResultContractError("REPEAT_RESULT_NOT_FOUND") from exc

    @staticmethod
    def _result_status(search_status: str) -> str:
        mapping = {
            SEARCH_SUCCESS: RESULT_READY_FOR_REVIEW,
            SEARCH_NO_CANDIDATES: RESULT_NO_CANDIDATES,
            SEARCH_UNAVAILABLE: RESULT_SEARCH_UNAVAILABLE,
            SEARCH_INCOMPLETE: RESULT_INCOMPLETE,
        }
        try:
            return mapping[search_status]
        except KeyError as exc:
            raise RepeatResultContractError("REPEAT_SEARCH_STATUS_INVALID") from exc

    @staticmethod
    def _candidate(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise RepeatResultContractError("REPEAT_CANDIDATE_INVALID")

        case_id = _text(item.get("case_id"))
        if not case_id:
            raise RepeatResultContractError("REPEAT_CANDIDATE_INVALID")

        retrieval_reasons = _unique_text(item.get("retrieval_reason"))
        matched_fields = _unique_text(item.get("matched_fields"))
        explanation: list[dict[str, Any]] = []

        for reason in retrieval_reasons:
            explanation.append({
                "type": "RETRIEVAL_REASON",
                "text": reason,
            })

        for field in matched_fields:
            explanation.append({
                "type": "MATCHED_FIELD",
                "field": field,
                "text": f"匹配维度：{_MATCHED_FIELD_LABELS.get(field, field)}",
            })

        explanation_status = "EXPLAINED" if explanation else "INSUFFICIENT"
        explanation_message = (
            None
            if explanation
            else "当前检索结果未返回足够的可解释关联依据；不能仅依据相似度分数判断 Repeat。"
        )

        evidence = deepcopy(item.get("evidence") or [])
        evidence_refs = deepcopy(item.get("evidence_refs") or [])
        root_causes = _unique_text(item.get("root_causes"))
        measures = _unique_text(item.get("measures"))

        return {
            "case_id": case_id,
            "title": item.get("title"),
            "historical_phenomenon": item.get("historical_phenomenon")
            or item.get("problem_description"),
            "retrieval_score": item.get("retrieval_score"),
            "rank": item.get("rank"),
            "why_relevant": explanation,
            "explanation_status": explanation_status,
            "explanation_message": explanation_message,
            "root_causes": root_causes,
            "measures": measures,
            "verification": item.get("verification"),
            "evidence_refs": evidence_refs,
            "evidence": evidence,
            "source_ref": item.get("source_ref"),
            "source_refs": deepcopy(item.get("source_refs") or []),
            "detail_status": item.get("detail_status") or SEARCH_INCOMPLETE,
            "detail_error": item.get("detail_error"),
            "case_status": item.get("case_status"),
        }
