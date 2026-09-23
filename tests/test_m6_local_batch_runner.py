from pathlib import Path
import json

from builder import local_batch_runner as module


def test_report_metrics_uses_report_fields():
    metrics = module._report_metrics({
        "repeat_decision": {"decision": "REPEAT", "confidence": 91},
        "summary": {"analysis_status": "SUCCESS"},
        "recommendation": {"overall_similarity": 83},
    })
    assert metrics["decision"] == "REPEAT"
    assert metrics["confidence"] == 91
    assert metrics["overall_similarity"] == 83


def test_snapshot_excludes_sensitive_by_default(tmp_path: Path):
    root = tmp_path / "project"
    run_root = root / "output/runs/RUN_TEST"
    (root / "output/reports/Q1").mkdir(parents=True)
    (root / "output/reports/Q1/report.json").write_text("{}", encoding="utf-8")
    (root / "knowledge/raw_query").mkdir(parents=True)
    (root / "knowledge/raw_query/Q1.json").write_text('{"secret":"x"}', encoding="utf-8")

    artifacts = module._snapshot_query_outputs(root, run_root, "Q1", False)
    assert "report_json" in artifacts
    assert "raw_query" not in artifacts
    assert not (run_root / "queries/Q1/debug_sensitive/Q1.json").exists()


def test_summary_markdown(tmp_path: Path):
    path = tmp_path / "summary.md"
    module._write_summary_markdown(path, {
        "run_id": "RUN_TEST", "engine_version": "V2.4", "status": "SUCCESS",
        "query_count": 1, "success_count": 1, "failed_count": 0,
        "skipped_count": 0, "success_rate": 100, "elapsed_seconds": 1.2,
        "queries": [{"query_id": "Q1", "status": "SUCCESS", "failed_stage": "", "elapsed_seconds": 1, "overall_similarity": 80, "confidence": 90}],
    })
    text = path.read_text(encoding="utf-8")
    assert "RUN_TEST" in text
    assert "Q1" in text
