from __future__ import annotations

import json
from pathlib import Path

from builder.query_artifact_cleaner import clean_orphan_query_artifacts, clean_query_artifacts
from builder.repeat_decision import _similarity_confidence
from presentation.construction.report_builder import ReportBuilder
from presentation.renderer.markdown_renderer import MarkdownRenderer


def test_query_cleanup_removes_same_query_and_orphans(tmp_path: Path) -> None:
    for relative in [
        "knowledge/raw_query", "knowledge/normalized_query", "knowledge/enriched_query",
        "knowledge/standard_query", "knowledge/retrieval_profile", "output/candidate_cases",
        "output/retrieval_results", "knowledge/analysis_context", "knowledge/similarity_analysis",
        "knowledge/solution_analysis", "knowledge/repeat_analysis",
    ]:
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge/raw_query/QUERY001.json").write_text("{}", encoding="utf-8")
    for qid in ("QUERY001", "QUERY002"):
        for relative in ["knowledge/normalized_query", "knowledge/enriched_query", "knowledge/standard_query", "knowledge/retrieval_profile", "output/candidate_cases", "output/retrieval_results"]:
            (tmp_path / relative / f"{qid}.json").write_text("{}", encoding="utf-8")
        for relative in ["knowledge/analysis_context", "knowledge/similarity_analysis", "knowledge/solution_analysis", "knowledge/repeat_analysis"]:
            d = tmp_path / relative / qid
            d.mkdir(parents=True, exist_ok=True)
            (d / "x.json").write_text("{}", encoding="utf-8")

    clean_query_artifacts(tmp_path, ["QUERY001"])
    assert (tmp_path / "knowledge/raw_query/QUERY001.json").exists()
    assert not (tmp_path / "knowledge/standard_query/QUERY001.json").exists()
    assert not (tmp_path / "knowledge/repeat_analysis/QUERY001").exists()

    result = clean_orphan_query_artifacts(tmp_path, ["QUERY001"])
    assert result["orphan_queries"] == 1
    assert not (tmp_path / "knowledge/standard_query/QUERY002.json").exists()
    assert not (tmp_path / "knowledge/repeat_analysis/QUERY002").exists()


def test_similarity_confidence_falls_back_to_scores() -> None:
    similarity = {
        "analysis": {
            "confidence": 0,
            "overall_score": 0,
            "dimensions": {"problem_object": {"score": 80}, "phenomenon": {"score": 90}},
        }
    }
    assert _similarity_confidence(similarity) == 0.85


def test_report_displays_best_and_other_candidate_scores() -> None:
    analysis = {
        "metadata": {"query_id": "QUERY001"},
        "final_decision": "LIKELY_REPEAT",
        "overall_confidence": 0,
        "best_case": {"case_id": "CASE-1", "final_rank": 1, "final_score": 85, "decision": "LIKELY_REPEAT", "confidence": 0, "decision_reason": "相似"},
        "analysis_status": "SUCCESS",
        "warnings": [],
        "candidates": [
            {
                "case_id": "CASE-1", "decision": "LIKELY_REPEAT", "confidence": 0,
                "decision_reason": "相似", "key_differences": [], "validation_required": [], "risks": [],
                "recommended_actions": [], "evidence_chain": [],
                "similarity": {"analysis": {"overall_score": 85, "confidence": 0, "dimensions": {"problem_object": {"score": 80}, "phenomenon": {"score": 90}}}},
                "solution": {}, "comparison_context": {},
            },
            {
                "case_id": "CASE-2", "decision": "RELATED_CASE", "confidence": 0,
                "decision_reason": "部分相似", "key_differences": [], "validation_required": [], "risks": [],
                "recommended_actions": [], "evidence_chain": [],
                "similarity": {"analysis": {"overall_score": 70, "confidence": 0.7, "dimensions": {"problem_object": {"score": 65}, "phenomenon": {"score": 75}}}},
                "solution": {}, "comparison_context": {},
            },
        ],
    }
    report = ReportBuilder().build(analysis)
    markdown = MarkdownRenderer().render(report)
    assert "综合置信度 | 39%" in markdown
    assert "综合相似度 | 85" in markdown
    assert "问题对象 | 80" in markdown
    assert "问题现象 | 90" in markdown
    assert "### CASE-2" in markdown
    assert "综合相似度：70" in markdown
    assert "问题对象 | 65" in markdown
