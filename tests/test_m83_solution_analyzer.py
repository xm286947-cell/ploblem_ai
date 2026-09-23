from __future__ import annotations

import json
from pathlib import Path

from builder.m83_solution_runner import run_m83_solution
from builder.solution_analyzer import SolutionAnalyzer
from parser.common import write_json


def _prepare(root: Path, with_similarity: bool = True) -> None:
    project = Path(__file__).resolve().parents[1]
    for rel in ["config/model.yaml", "prompts/solution_analyzer.md", "schema/solution_analysis.schema.json", "tests/samples/mock_solution_response.json"]:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text((project / rel).read_text(encoding="utf-8"), encoding="utf-8")
    for p in [root / "knowledge/analysis_context/Q1", root / "knowledge/similarity_analysis/Q1", root / "knowledge/solution_analysis", root / "output/logs"]:
        p.mkdir(parents=True, exist_ok=True)
    write_json(root / "knowledge/analysis_context/Q1/C1.json", {
        "context_version": "M8.1-C1", "query_id": "Q1", "case_id": "C1",
        "query": {"standard_query": {"problem": {}}, "retrieval_profile": {}},
        "candidate": {"rank": 1, "score": 0.88},
        "case": {"standard_case": {}, "enriched_case": {"solution": "修正控制逻辑"}, "retrieval_document": {}, "raw_evidence": {}, "embedding_metadata": {}},
        "evidence": {"available_sources": [], "retrieval_text": "", "report_filename": "", "matched_report_path": "", "sections": [], "unclassified_blocks": []},
        "source_paths": {"candidate_file": "output/candidate_cases/Q1.json"},
        "quality": {"status": "COMPLETE", "missing_sources": [], "quality_flags": []},
        "generated_at": "2026-01-01T00:00:00+00:00"
    })
    if with_similarity:
        write_json(root / "knowledge/similarity_analysis/Q1/C1.json", {
            "metadata": {"query_id": "Q1", "case_id": "C1"},
            "analysis": {"overall_score": 88, "overall_level": "HIGH"},
            "analysis_status": "SUCCESS"
        })


def test_mock_solution_success(tmp_path: Path) -> None:
    _prepare(tmp_path)
    result = run_m83_solution(tmp_path, query_id="Q1", mock=True, overwrite=True)
    assert result["success"] == 1
    data = json.loads((tmp_path / "knowledge/solution_analysis/Q1/C1.json").read_text(encoding="utf-8"))
    assert data["analysis"]["effectiveness"] == "EFFECTIVE"
    assert data["analysis"]["applicability"] == "PARTIAL_REUSE"
    assert data["similarity_reference"]["overall_score"] == 88


def test_missing_similarity_is_warning_not_failure(tmp_path: Path) -> None:
    _prepare(tmp_path, with_similarity=False)
    result = run_m83_solution(tmp_path, query_id="Q1", mock=True, overwrite=True)
    assert result["success"] == 1
    assert result["similarity_missing"] == 1
    data = json.loads((tmp_path / "knowledge/solution_analysis/Q1/C1.json").read_text(encoding="utf-8"))
    assert data["similarity_reference"]["overall_level"] == "UNKNOWN"
    assert any(w["code"] == "SIMILARITY_ANALYSIS_MISSING" for w in data["warnings"])


def test_skip_ai_degrades_cleanly(tmp_path: Path) -> None:
    _prepare(tmp_path)
    result = run_m83_solution(tmp_path, query_id="Q1", skip_ai=True, overwrite=True)
    assert result["skipped"] == 1
    data = json.loads((tmp_path / "knowledge/solution_analysis/Q1/C1.json").read_text(encoding="utf-8"))
    assert data["analysis"]["effectiveness"] == "UNKNOWN"


def test_invalid_ai_output_falls_back(tmp_path: Path) -> None:
    _prepare(tmp_path)
    bad = tmp_path / "tests/samples/bad_solution.json"
    bad.write_text('{"effectiveness":"EFFECTIVE"}', encoding="utf-8")
    from builder.ai_client import MockAIClient
    analyzer = SolutionAnalyzer(tmp_path, client=MockAIClient(bad))
    context = json.loads((tmp_path / "knowledge/analysis_context/Q1/C1.json").read_text(encoding="utf-8"))
    similarity = json.loads((tmp_path / "knowledge/similarity_analysis/Q1/C1.json").read_text(encoding="utf-8"))
    result = analyzer.analyze(context, similarity)
    assert result["analysis_status"] == "AI_OUTPUT_INVALID"
