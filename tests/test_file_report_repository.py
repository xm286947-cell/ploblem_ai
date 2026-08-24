from __future__ import annotations

import json
from pathlib import Path

import pytest

from presentation.construction.report_builder import ReportBuilder
from presentation.repository.file_report_repository import FileReportRepository


def _report():
    return ReportBuilder().build({
        "metadata": {"query_id": "Q1"},
        "final_decision": "LIKELY_REPEAT",
        "overall_confidence": 0.88,
        "best_case": {"case_id": "C1"},
        "candidates": [],
        "analysis_status": "SUCCESS",
        "warnings": [],
    })


def test_repository_saves_json_and_markdown(tmp_path: Path) -> None:
    repository = FileReportRepository(tmp_path / "reports")

    paths = repository.save("Q1", _report())

    assert paths["json"] == tmp_path / "reports" / "Q1" / "report.json"
    assert paths["markdown"] == tmp_path / "reports" / "Q1" / "report.md"
    assert paths["json"].exists()
    assert paths["markdown"].exists()
    assert json.loads(paths["json"].read_text(encoding="utf-8"))["summary"]["query_id"] == "Q1"
    assert "重复问题辅助分析报告（Q1）" in paths["markdown"].read_text(encoding="utf-8")


def test_repository_loads_saved_report(tmp_path: Path) -> None:
    repository = FileReportRepository(tmp_path)
    repository.save("Q1", _report())

    loaded = repository.load("Q1")

    assert loaded["repeat_decision"]["decision"] == "LIKELY_REPEAT"


@pytest.mark.parametrize("query_id", ["", "../Q1", "a/b", ".", ".."])
def test_repository_rejects_invalid_query_id(tmp_path: Path, query_id: str) -> None:
    repository = FileReportRepository(tmp_path)

    with pytest.raises(ValueError):
        repository.save(query_id, _report())
