from __future__ import annotations

from presentation.contract.analysis_result import AnalysisResult
from presentation.construction.report_builder import ReportBuilder


def _analysis() -> dict:
    return {
        "metadata": {"query_id": "Q1", "generated_at": "2026-07-23T00:00:00+00:00"},
        "final_decision": "LIKELY_REPEAT",
        "overall_confidence": 0.88,
        "best_case": {"case_id": "C1", "final_rank": 1},
        "candidates": [{
            "case_id": "C1",
            "retrieval_rank": 1,
            "final_rank": 1,
            "final_score": 91.5,
            "decision": "LIKELY_REPEAT",
            "confidence": 0.88,
            "decision_reason": "关键机制一致",
            "evidence_chain": ["CAN接收队列发生拥堵"],
            "key_differences": [],
            "validation_required": ["确认总线负载"],
            "risks": ["高负载下可能重启"],
            "recommended_actions": ["复用接收限流方案"],
        }],
        "analysis_status": "SUCCESS",
        "warnings": [],
    }


def test_analysis_result_roundtrip() -> None:
    result = AnalysisResult.from_mapping(_analysis())
    assert result.query_id == "Q1"
    assert result.to_dict()["final_decision"] == "LIKELY_REPEAT"


def test_report_builder_maps_without_decision_logic() -> None:
    report = ReportBuilder().build(_analysis()).to_dict()
    assert report["metadata"]["contract_version"] == "2.1"
    assert report["repeat_decision"]["decision"] == "LIKELY_REPEAT"
    assert report["similar_cases"][0]["case_id"] == "C1"
    assert report["recommendations"][0]["action"] == "复用接收限流方案"
    assert report["evidence"][0]["evidence"] == "CAN接收队列发生拥堵"
