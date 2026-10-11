"""#624: Preserve published evidence and distinguish M8.4 execution status."""
from __future__ import annotations

import json
from pathlib import Path

from presentation.delivery_service import DeliveryService
from quality_knowledge.repeat_risk.agent_analysis import RepeatAgentAnalysisService


def _render(tmp_path: Path, candidates: list[dict]):
    analysis = RepeatAgentAnalysisService._analysis_mapping(
        "RQ-SYNTHETIC-624", candidates, "SUCCESS", []
    )
    delivered = DeliveryService(tmp_path).deliver(analysis)
    report = json.loads(Path(delivered["report_json"]).read_text(encoding="utf-8"))
    markdown = Path(delivered["report_markdown"]).read_text(encoding="utf-8")
    return report, markdown


def _historical_evidence():
    return [
        {
            "evidence_id": f"EV-{index}",
            "source_version": f"KREV-{index}",
            "source_ref": f"MAJOR_SOURCE_DOCUMENT:ITR-SYN@KREV-{index}",
            "source_id": "ITR-SYN",
            "file_name": "synthetic_8d.pdf",
            "raw_text": "DO_NOT_COPY_RAW_PDF_TEXT_TO_REPEAT_REPORT",
        }
        for index in range(1, 5)
    ]


def test_m84_off_has_no_ai_decision_and_keeps_four_historical_evidence(tmp_path):
    case = {
        "case_id": "HCASE-SYNTHETIC-624",
        "rank": 1,
        "evidence": _historical_evidence(),
        "ai_recommendation": {"status": "DISABLED", "decision": None},
        "agent_similarity": {"analysis": {"confidence": 0.76}},
        "agent_solution": {"analysis": {}},
    }
    report, markdown = _render(tmp_path, [case])
    decision = report["repeat_decision"]
    assert decision["recommendation_status"] == "DISABLED"
    assert decision["decision_source"] == "NOT_EXECUTED"
    assert decision["confidence_source"] == "M8.2_SIMILARITY"
    assert len(report["evidence"]) == 4
    assert {item["evidence"]["evidence_id"] for item in report["evidence"]} == {
        "EV-1", "EV-2", "EV-3", "EV-4"
    }
    for item in report["evidence"]:
        evidence = item["evidence"]
        assert evidence["source_version"] and evidence["source_ref"]
        assert "raw_text" not in evidence
        assert evidence["evidence_id"] in markdown
    assert "DO_NOT_COPY_RAW_PDF_TEXT_TO_REPEAT_REPORT" not in markdown
    assert "## 1. AI初步判断" not in markdown
    assert "M8.4 决策 Agent | 未启用（未调用 M8.4）" in markdown
    assert "未生成 AI 重复判断" in markdown


def test_m84_on_preserves_real_agent_recommendation_semantics(tmp_path):
    case = {
        "case_id": "HCASE-SYNTHETIC-ON",
        "rank": 1,
        "evidence": [_historical_evidence()[0]],
        "ai_recommendation": {
            "status": "SUCCESS",
            "decision": "LIKELY_REPEAT",
            "confidence": 0.9,
            "evidence_chain": ["M8.4 original cited reason"],
        },
        "agent_similarity": {"analysis": {"confidence": 0.7}},
    }
    report, markdown = _render(tmp_path, [case])
    decision = report["repeat_decision"]
    assert decision["recommendation_status"] == "SUCCESS"
    assert decision["decision_source"] == "M8.4_RECOMMENDATION"
    assert decision["confidence_source"] == "M8.4_RECOMMENDATION"
    assert decision["decision"] == "LIKELY_REPEAT"
    assert decision["confidence"] == 0.9
    assert len(report["evidence"]) == 2
    assert "## 1. AI初步判断" in markdown
    assert "EV-1" in markdown


def test_m84_failure_does_not_look_like_success(tmp_path):
    case = {
        "case_id": "HCASE-SYNTHETIC-FAILED",
        "rank": 1,
        "ai_recommendation": {"status": "FAILED", "decision": None},
        "agent_similarity": {"analysis": {"confidence": 0.63}},
    }
    report, markdown = _render(tmp_path, [case])
    assert report["evidence"] == []
    assert report["repeat_decision"]["recommendation_status"] == "FAILED"
    assert report["repeat_decision"]["decision_source"] == "UNAVAILABLE"
    assert "## 1. AI初步判断" not in markdown
    assert "M8.4 决策 Agent | 未完成" in markdown


def test_source_ref_fallback_without_raw_source_document(tmp_path):
    case = {
        "case_id": "HCASE-SYNTHETIC-REF",
        "rank": 1,
        "evidence_refs": [{"source_id": "ITR-SYN", "file_name": "8d.pdf", "section": "analysis:ROOT_CAUSE"}],
        "ai_recommendation": {"status": "DISABLED"},
    }
    report, markdown = _render(tmp_path, [case])
    assert len(report["evidence"]) == 1
    assert report["evidence"][0]["evidence"]["source_id"] == "ITR-SYN"
    assert "ITR-SYN" in markdown


def test_generic_legacy_report_contract_is_additively_unchanged(tmp_path):
    from presentation.construction.report_builder import ReportBuilder
    report = ReportBuilder().build({
        "metadata": {"query_id": "RQ-LEGACY-624"},
        "final_decision": "INSUFFICIENT_EVIDENCE",
        "candidates": [],
    })
    decision = report.to_dict()["repeat_decision"]
    assert "recommendation_status" not in decision
    assert "decision_source" not in decision
    assert "confidence_source" not in decision


def test_m84_off_multiple_cases_stay_auxiliary(tmp_path):
    candidates = [
        {
            "case_id": f"HCASE-SYN-{index}",
            "rank": index,
            "ai_recommendation": {"status": "DISABLED"},
            "agent_similarity": {"analysis": {"confidence": 0.64}},
        }
        for index in (1, 2)
    ]
    report, markdown = _render(tmp_path, candidates)
    assert report["repeat_decision"]["recommendation_status"] == "DISABLED"
    assert "## 8. 其他候选案例" in markdown
    assert "辅助分析置信度" in markdown
    assert "未执行 M8.4（人工待确认）" in markdown
    assert "## 1. AI初步判断" not in markdown
