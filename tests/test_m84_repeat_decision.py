from __future__ import annotations

import json
from pathlib import Path

from builder.m84_repeat_runner import run_m84_decision
from parser.common import write_json


def _copy(project: Path, root: Path, rel: str) -> None:
    dst = root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text((project / rel).read_text(encoding="utf-8"), encoding="utf-8")


def _prepare(root: Path, cases: int = 2, missing_solution: bool = False) -> None:
    project = Path(__file__).resolve().parents[1]
    for rel in [
        "config/model.yaml", "config/repeat_decision.yaml", "prompts/repeat_decision.md",
        "schema/repeat_analysis.schema.json", "tests/samples/mock_repeat_decision_response.json",
    ]:
        _copy(project, root, rel)
    (root / "output/logs").mkdir(parents=True, exist_ok=True)
    for i in range(1, cases + 1):
        cid = f"C{i}"
        write_json(root / f"knowledge/analysis_context/Q1/{cid}.json", {
            "query_id": "Q1", "case_id": cid,
            "query": {"standard_query": {"problem": {"problem_summary": "CAN接收拥堵"}}},
            "candidate": {"rank": i, "score": 0.9 - i * 0.05},
            "case": {"enriched_case": {"root_cause": "接收处理设计不足"}},
            "evidence": {}, "quality": {"status": "COMPLETE"}
        })
        write_json(root / f"knowledge/similarity_analysis/Q1/{cid}.json", {
            "analysis": {"overall_score": 90 - i, "overall_level": "HIGH"}, "analysis_status": "SUCCESS"
        })
        if not missing_solution:
            write_json(root / f"knowledge/solution_analysis/Q1/{cid}.json", {
                "analysis": {"applicability": "PARTIAL_REUSE", "confidence": 0.8}, "analysis_status": "SUCCESS"
            })


def test_mock_repeat_decision_success(tmp_path: Path) -> None:
    _prepare(tmp_path)
    summary = run_m84_decision(tmp_path, query_id="Q1", mock=True, overwrite=True)
    assert summary["success"] == 1
    data = json.loads((tmp_path / "knowledge/repeat_analysis/Q1/repeat_analysis.json").read_text(encoding="utf-8"))
    assert data["final_decision"] == "LIKELY_REPEAT"
    assert data["best_case"]["case_id"] == "C1"
    assert len(data["candidates"]) == 2
    assert data["candidates"][0]["final_rank"] == 1
    assert data["candidates"][0]["evidence_chain"]
    report_md = tmp_path / "knowledge/repeat_analysis/Q1/report.md"
    assert report_md.exists()
    assert "# 重复问题辅助分析报告（Q1）" in report_md.read_text(encoding="utf-8")


def test_missing_solution_is_warning_not_failure(tmp_path: Path) -> None:
    _prepare(tmp_path, cases=1, missing_solution=True)
    summary = run_m84_decision(tmp_path, query_id="Q1", mock=True, overwrite=True)
    assert summary["success"] == 1
    assert summary["solution_missing"] == 1
    data = json.loads((tmp_path / "knowledge/repeat_analysis/Q1/repeat_analysis.json").read_text(encoding="utf-8"))
    assert any(w["code"] == "SOLUTION_ANALYSIS_MISSING" for w in data["warnings"])


def test_skip_ai_degrades_cleanly(tmp_path: Path) -> None:
    _prepare(tmp_path, cases=1)
    summary = run_m84_decision(tmp_path, query_id="Q1", skip_ai=True, overwrite=True)
    assert summary["skipped"] == 1
    data = json.loads((tmp_path / "knowledge/repeat_analysis/Q1/repeat_analysis.json").read_text(encoding="utf-8"))
    assert data["final_decision"] == "INSUFFICIENT_EVIDENCE"
    assert data["analysis_status"] == "SKIPPED"


def test_no_context_returns_failure(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    for rel in ["config/model.yaml", "config/repeat_decision.yaml", "prompts/repeat_decision.md", "schema/repeat_analysis.schema.json", "tests/samples/mock_repeat_decision_response.json"]:
        _copy(project, tmp_path, rel)
    (tmp_path / "output/logs").mkdir(parents=True, exist_ok=True)
    summary = run_m84_decision(tmp_path, query_id="Q404", mock=True, overwrite=True)
    assert summary["failed"] == 1
