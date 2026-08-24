from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List
import hashlib
import json


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _values(items: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for item in items or []:
        if isinstance(item, dict):
            value = _text(item.get("value"))
        else:
            value = _text(item)
        if value and value not in result:
            result.append(value)
    return result


def _pick_classification(case: dict) -> dict:
    classification = case.get("classification", {})
    ai = classification.get("ai_inferred", {})
    report = classification.get("report_verified", {})
    original = classification.get("original", {})

    if _text(ai.get("cause_level1")) or _text(ai.get("cause_level2")):
        return {
            "cause_level1": _text(ai.get("cause_level1")),
            "cause_level2": _text(ai.get("cause_level2")),
            "source": "AI",
        }
    if _text(report.get("cause_level1")) or _text(report.get("cause_level2")):
        return {
            "cause_level1": _text(report.get("cause_level1")),
            "cause_level2": _text(report.get("cause_level2")),
            "source": "REPORT",
        }
    if _text(original.get("cause_level1")) or _text(original.get("cause_level2")):
        return {
            "cause_level1": _text(original.get("cause_level1")),
            "cause_level2": _text(original.get("cause_level2")),
            "source": "ORIGINAL",
        }
    return {"cause_level1": "", "cause_level2": "", "source": "EMPTY"}


def _cause_text(group: dict) -> str:
    values: List[str] = []
    for kind in ("occurrence", "escape"):
        detail = group.get(kind, {})
        for field in ("standard", "report", "original"):
            value = _text(detail.get(field))
            if value:
                values.append(value)
                break
    return "；".join(values)


def _dedupe(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


class RetrievalDocumentBuilder:
    def build(self, case: dict, source_case_path: str) -> dict:
        metadata = case["metadata"]
        business = case["business_context"]
        problem = case["problem"]
        analysis = case["analysis"]
        solution = case["solution"]
        knowledge = case["knowledge"]
        classification = _pick_classification(case)

        title = (
            _text(knowledge.get("normalized_problem"))
            or _text(problem.get("standard_description"))
            or _text(problem.get("report_description"))
            or _text(problem.get("original_description"))
            or metadata["case_id"]
        )

        tags = _dedupe(
            knowledge.get("phenomenon_tags", [])
            + knowledge.get("failure_object_tags", [])
            + knowledge.get("trigger_tags", [])
            + knowledge.get("failure_mechanism_tags", [])
            + knowledge.get("cause_tags", [])
            + knowledge.get("solution_tags", [])
            + knowledge.get("keywords", [])
        )

        text = _text(knowledge.get("retrieval_text"))
        if not text:
            parts = [
                f"案例：{metadata.get('case_id','')} / {metadata.get('itr_id','')}",
                f"组织：{business.get('ipmt','')} / {business.get('spdt','')}",
                f"问题：{title}",
                f"原始描述：{problem.get('original_description','')}",
                f"报告描述：{problem.get('report_description','')}",
                f"TRC：{_cause_text(analysis.get('trc',{}))}",
                f"MRC：{_cause_text(analysis.get('mrc',{}))}",
                f"根因：{'；'.join(_values(analysis.get('root_cause',[])))}",
                f"失效机制：{'；'.join(_values(analysis.get('failure_mechanism',[])))}",
                f"纠正措施：{'；'.join(_values(solution.get('corrective_actions',[])))}",
                f"预防措施：{'；'.join(_values(solution.get('preventive_actions',[])))}",
                f"可复用措施：{'；'.join(_values(solution.get('reusable_actions',[])))}",
                f"分类：{classification['cause_level1']} / {classification['cause_level2']}",
                f"标签：{'、'.join(tags)}",
            ]
            text = "\n".join(part for part in parts if not part.endswith("："))

        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        case_id = _text(metadata.get("case_id"))
        return {
            "document_id": f"DOC-{case_id}",
            "case_id": case_id,
            "itr_id": _text(metadata.get("itr_id")),
            "source_case_path": source_case_path,
            "organization": {
                "ipmt": _text(business.get("ipmt")),
                "spdt": _text(business.get("spdt")),
                "responsible_department_level2": _text(
                    business.get("responsible_department_level2")
                ),
            },
            "classification": classification,
            "filters": {
                "assessment_year": _text(metadata.get("assessment_year")),
                "assessment_month": _text(metadata.get("assessment_month")),
                "product": _text(business.get("product")),
                "domain": _text(business.get("domain")),
                "has_report": bool(_text(metadata.get("source_report"))),
                "classification_conflict": bool(
                    case.get("classification", {}).get("classification_conflict", False)
                ),
            },
            "title": title,
            "text": text,
            "tags": tags,
            "quality_flags": _dedupe(knowledge.get("quality_flags", [])),
            "content_hash": content_hash,
            "generated_at": now,
        }
