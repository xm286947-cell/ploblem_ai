from __future__ import annotations

import json
from pathlib import Path

from builder.m82_similarity_runner import run_m82_similarity
from builder.similarity_analyzer import SimilarityAnalyzer
from parser.common import write_json


def _prepare(root: Path) -> None:
    for p in [root / "knowledge/analysis_context/Q1", root / "knowledge/similarity_analysis", root / "output/logs", root / "config", root / "prompts", root / "schema", root / "tests/samples"]:
        p.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parents[1]
    for rel in ["config/model.yaml", "prompts/similarity_analyzer.md", "schema/similarity_analysis.schema.json", "tests/samples/mock_similarity_response.json"]:
        src = project / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    write_json(root / "knowledge/analysis_context/Q1/C1.json", {
        "context_version": "M8.1-C1", "query_id": "Q1", "case_id": "C1",
        "query": {"standard_query": {"problem": {}}, "retrieval_profile": {}},
        "candidate": {"rank": 1, "score": 0.88},
        "case": {"standard_case": {}, "enriched_case": {}, "retrieval_document": {}, "raw_evidence": {}, "embedding_metadata": {}},
        "evidence": {"available_sources": [], "retrieval_text": "", "report_filename": "", "matched_report_path": "", "sections": [], "unclassified_blocks": []},
        "source_paths": {"candidate_file": "output/candidate_cases/Q1.json"},
        "quality": {"status": "COMPLETE", "missing_sources": [], "quality_flags": []},
        "generated_at": "2026-01-01T00:00:00+00:00"
    })


def test_mock_similarity_success(tmp_path: Path) -> None:
    _prepare(tmp_path)
    result = run_m82_similarity(tmp_path, query_id="Q1", mock=True, overwrite=True)
    assert result["success"] == 1
    data = json.loads((tmp_path / "knowledge/similarity_analysis/Q1/C1.json").read_text(encoding="utf-8"))
    assert data["analysis"]["overall_score"] == 88
    assert data["analysis_status"] == "SUCCESS"


def test_skip_ai_degrades_cleanly(tmp_path: Path) -> None:
    _prepare(tmp_path)
    result = run_m82_similarity(tmp_path, query_id="Q1", skip_ai=True, overwrite=True)
    assert result["skipped"] == 1
    data = json.loads((tmp_path / "knowledge/similarity_analysis/Q1/C1.json").read_text(encoding="utf-8"))
    assert data["analysis"]["overall_level"] == "UNKNOWN"
    assert data["analysis_status"] == "SKIPPED"


def test_missing_context(tmp_path: Path) -> None:
    (tmp_path / "knowledge/analysis_context").mkdir(parents=True)
    (tmp_path / "knowledge/similarity_analysis").mkdir(parents=True)
    (tmp_path / "output/logs").mkdir(parents=True)
    _prepare(tmp_path)
    result = run_m82_similarity(tmp_path, query_id="NONE", mock=True)
    assert result["failed"] == 1


def test_invalid_ai_output_falls_back(tmp_path: Path) -> None:
    _prepare(tmp_path)
    bad = tmp_path / "tests/samples/bad.json"
    bad.write_text('{"overall_score": 99}', encoding="utf-8")
    from builder.ai_client import MockAIClient
    analyzer = SimilarityAnalyzer(tmp_path, client=MockAIClient(bad))
    context = json.loads((tmp_path / "knowledge/analysis_context/Q1/C1.json").read_text(encoding="utf-8"))
    result = analyzer.analyze(context)
    assert result["analysis_status"] == "AI_OUTPUT_INVALID"
