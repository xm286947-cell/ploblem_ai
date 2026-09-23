from pathlib import Path

from builder.m2_runner import run_m2


ROOT = Path(__file__).resolve().parents[1]


def test_m2_end_to_end(tmp_path):
    result = run_m2(
        project_root=ROOT,
        excel_path=ROOT / "tests" / "samples" / "cases_m2.xlsx",
        reports_dir=ROOT / "tests" / "samples" / "reports",
    )
    assert result["excel"]["total_rows"] == 5
    assert "ITR-004" in result["excel"]["duplicate_itr_ids"]
    counts = result["report_matching"]["match_type_counts"]
    assert counts["EXACT_FILENAME"] == 2
    assert counts["NO_REPORT_NAME"] == 1
    assert counts["NOT_FOUND"] == 1
    assert counts["AMBIGUOUS"] == 1
