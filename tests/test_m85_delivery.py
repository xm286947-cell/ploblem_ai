from __future__ import annotations

import json
from pathlib import Path

from builder.m85_delivery_runner import run_m85_delivery


def _analysis(query_id: str = "Q1") -> dict:
    return {
        "metadata": {"query_id": query_id},
        "final_decision": "LIKELY_REPEAT",
        "overall_confidence": 0.82,
        "best_case": {"case_id": "C1"},
        "candidates": [{
            "case_id": "C1",
            "decision": "LIKELY_REPEAT",
            "confidence": 0.82,
            "decision_reason": "根因方向相近",
            "recommended_actions": ["复核历史措施适用性"],
        }],
        "analysis_status": "SUCCESS",
        "warnings": [],
    }


def _write_analysis(root: Path, query_id: str = "Q1") -> None:
    target = root / "knowledge/repeat_analysis" / query_id / "repeat_analysis.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(_analysis(query_id), ensure_ascii=False), encoding="utf-8")


def test_delivery_creates_official_report_and_index(tmp_path: Path) -> None:
    _write_analysis(tmp_path)

    summary = run_m85_delivery(tmp_path, query_id="Q1", overwrite=True)

    assert summary["success"] == 1
    report_json = tmp_path / "output/reports/Q1/report.json"
    report_md = tmp_path / "output/reports/Q1/report.md"
    index_json = tmp_path / "output/reports/report_index.json"
    index_md = tmp_path / "output/reports/report_index.md"
    assert report_json.exists() and report_md.exists()
    assert index_json.exists() and index_md.exists()
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert payload["metadata"]["delivery_contract_name"] == "REPEAT_CASE_REPORT"
    assert payload["metadata"]["delivery_contract_version"] == "1.0"
    assert payload["traceability"]["source_artifact"].endswith("repeat_analysis.json")
    assert "Q1/report.md" in index_md.read_text(encoding="utf-8")


def test_delivery_missing_analysis_is_reported(tmp_path: Path) -> None:
    summary = run_m85_delivery(tmp_path, query_id="Q404")
    assert summary["failed"] == 1
    assert summary["errors"][0]["error"] == "REPEAT_ANALYSIS_NOT_FOUND"


def test_delivery_skips_existing_but_rebuilds_index(tmp_path: Path) -> None:
    _write_analysis(tmp_path)
    first = run_m85_delivery(tmp_path, query_id="Q1", overwrite=True)
    second = run_m85_delivery(tmp_path, query_id="Q1", overwrite=False)
    assert first["success"] == 1
    assert second["skipped"] == 1
    index = json.loads((tmp_path / "output/reports/report_index.json").read_text(encoding="utf-8"))
    assert index["report_count"] == 1
