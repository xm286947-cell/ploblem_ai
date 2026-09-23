from pathlib import Path

import builder.analysis_pipeline_runner as pipeline


def test_pipeline_isolates_query_failure(tmp_path, monkeypatch):
    raw = tmp_path / "knowledge/raw_query"
    raw.mkdir(parents=True)
    (raw / "Q1.json").write_text("{}", encoding="utf-8")
    (raw / "Q2.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pipeline, "run_m72_pipeline", lambda root, query_id, **kwargs: {"failed": 1 if query_id == "Q1" else 0})
    monkeypatch.setattr(pipeline, "run_m73_profile", lambda *args, **kwargs: {"failed": 0})
    monkeypatch.setattr(pipeline, "run_m73_retrieve", lambda *args, **kwargs: {"failed": 0})
    monkeypatch.setattr(pipeline, "run_m81_load", lambda *args, **kwargs: {"failed": 0})
    monkeypatch.setattr(pipeline, "run_m82_similarity", lambda *args, **kwargs: {"failed": 0})
    monkeypatch.setattr(pipeline, "run_m83_solution", lambda *args, **kwargs: {"failed": 0})
    monkeypatch.setattr(pipeline, "run_m84_decision", lambda *args, **kwargs: {"failed": 0})
    monkeypatch.setattr(pipeline, "run_m85_delivery", lambda *args, **kwargs: {"failed": 0})

    result = pipeline.run_analysis_pipeline(tmp_path, from_stage="enrich")

    assert result["status"] == "PARTIAL_SUCCESS"
    assert result["success"] == 1
    assert result["failed"] == 1
    assert result["queries"][0]["failed_stage"] == "enrich"
    assert result["queries"][1]["status"] == "SUCCESS"
    assert (tmp_path / "output/logs/analysis_pipeline_summary.json").exists()


def test_pipeline_can_resume_from_decision(tmp_path, monkeypatch):
    raw = tmp_path / "knowledge/raw_query"
    raw.mkdir(parents=True)
    (raw / "Q1.json").write_text("{}", encoding="utf-8")
    called = []
    monkeypatch.setattr(pipeline, "run_m84_decision", lambda *args, **kwargs: called.append("decision") or {"failed": 0})
    monkeypatch.setattr(pipeline, "run_m85_delivery", lambda *args, **kwargs: called.append("delivery") or {"failed": 0})

    result = pipeline.run_analysis_pipeline(tmp_path, query_id="Q1", from_stage="decision")

    assert result["status"] == "SUCCESS"
    assert called == ["decision", "delivery"]
    assert [s["stage"] for s in result["queries"][0]["stages"]] == ["decision", "delivery"]


def test_pipeline_stops_when_parse_fails(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("bad excel")
    monkeypatch.setattr(pipeline, "run_m71_query", fail)

    result = pipeline.run_analysis_pipeline(tmp_path)

    assert result["status"] == "FAILED"
    assert result["parse"]["status"] == "FAILED"
