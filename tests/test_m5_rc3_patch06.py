from __future__ import annotations

from builder.similarity_score import calculate_similarity_score, dimension_scores
from builder.confidence_calculator import calculate_confidence
from builder.context_filter import evaluate_context
from builder.repeat_decision import RepeatDecisionEngine
from presentation.construction.report_builder import ReportBuilder
from presentation.renderer.markdown_renderer import MarkdownRenderer


def _similarity() -> dict:
    return {
        "analysis": {
            "confidence": 0.8,
            "overall_score": 77,
            "dimensions": {
                "problem_object": {"score": 80, "query_evidence": ["电机"], "case_evidence": ["电机"]},
                "phenomenon": {"score": 90, "query_evidence": ["断线"], "case_evidence": ["断线"]},
                "organization_context": {"score": 10, "query_evidence": ["A"], "case_evidence": ["B"]},
                "root_cause": {"score": None, "query_evidence": [], "case_evidence": []},
            },
            "key_similarities": ["问题对象一致"],
        }
    }


def _context() -> dict:
    return {
        "query": {"standard_query": {"organization": {"IPMT": "传动IPMT", "SPDT": "低压变频器SPDT", "责任部门（二级）": "软件部"}}},
        "case": {"enriched_case": {"IPMT": "传动IPMT", "SPDT": "低压变频器SPDT", "责任部门(二级)": "测试部"}},
        "quality": {"status": "PARTIAL"},
    }


def test_similarity_excludes_organization_context() -> None:
    assert dimension_scores(_similarity()) == {"problem_object": 80.0, "phenomenon": 90.0, "root_cause": None}
    assert calculate_similarity_score(_similarity()) == 85.0


def test_confidence_is_not_similarity_score() -> None:
    details = calculate_confidence(_similarity(), _context(), 0.8)
    assert 0 < details["score"] < 1
    assert details["score"] != 0.85
    assert details["score_coverage"] == 0.6667


def test_context_filter_has_no_score() -> None:
    result = evaluate_context(_context())
    assert result["level"] == "中"
    assert "score" not in result
    assert [x["status"] for x in result["details"]] == ["一致", "一致", "不同"]


def test_report_separates_similarity_confidence_and_context() -> None:
    candidate = {
        "case_id": "CASE-1", "decision": "LIKELY_REPEAT", "confidence": 0.8,
        "decision_reason": "需要人工确认", "key_differences": [], "validation_required": [],
        "risks": [], "recommended_actions": [], "evidence_chain": [],
        "similarity": _similarity(), "solution": {}, "comparison_context": _context(),
        "similarity_score": 85.0,
        "dimension_scores": {"problem_object": 80.0, "phenomenon": 90.0, "root_cause": None},
        "confidence_details": calculate_confidence(_similarity(), _context(), 0.8),
        "context_applicability": evaluate_context(_context()),
        "recommendation_level": "★★★☆☆", "recommendation_reasons": ["问题对象一致"],
    }
    analysis = {
        "metadata": {"query_id": "QUERY001"}, "final_decision": "LIKELY_REPEAT",
        "overall_confidence": candidate["confidence_details"]["score"],
        "best_case": {"case_id": "CASE-1", "final_rank": 1, "final_score": 80, "decision": "LIKELY_REPEAT", "confidence": candidate["confidence_details"]["score"], "decision_reason": "需要人工确认"},
        "candidates": [candidate], "analysis_status": "SUCCESS", "warnings": [],
    }
    markdown = MarkdownRenderer().render(ReportBuilder().build(analysis))
    assert "综合相似度 | 85" in markdown
    assert "判断置信度依据" in markdown
    assert "组织适用性（仅用于筛选，不参与相似度评分）" in markdown
    assert "组织上下文 |" not in markdown
    assert "问题对象 | 80" in markdown
    assert "问题现象 | 90" in markdown
    assert "问题根因 | 未评分" in markdown


def test_recommendation_level_thresholds() -> None:
    assert RepeatDecisionEngine._recommendation_level(91, 0.91, "高") == "★★★★★"
    assert RepeatDecisionEngine._recommendation_level(82, 0.82, "中") == "★★★★☆"
    assert RepeatDecisionEngine._recommendation_level(55, 0.95, "高") == "★☆☆☆☆"
