import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.services.v2_batch_analysis_service import V2BatchAnalysisError, V2BatchAnalysisService
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def test_batch_analysis_runs_two_issues_concurrently_and_isolates_failure():
    barrier = threading.Barrier(2)
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def fake_run(_service, knowledge_id, _request):
        nonlocal active, maximum_active
        if knowledge_id == "K-BAD":
            raise RuntimeError("TEST_ISSUE_FAILED")
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        return SimpleNamespace(status="COMPLETED", analysis_set_id="AS-" + knowledge_id, warnings=[])

    service = V2BatchAnalysisService(object(), object())
    with patch("quality_knowledge.services.v2_batch_analysis_service.V2AnalysisService.run", fake_run):
        result = service.run(["K-2", "K-1", "K-2", "K-BAD"], concurrency=2)

    assert maximum_active == 2
    assert [item["knowledge_id"] for item in result["items"]] == ["K-2", "K-1", "K-BAD"]
    assert result["succeeded"] == 2 and result["partial"] == 0 and result["failed"] == 1
    assert result["items"][-1]["error"] == "TEST_ISSUE_FAILED"


@pytest.mark.parametrize("concurrency", [0, 5, "two"])
def test_batch_analysis_rejects_unsafe_concurrency(concurrency):
    with pytest.raises(V2BatchAnalysisError, match="BATCH_ANALYSIS_CONCURRENCY_INVALID"):
        V2BatchAnalysisService(object(), object()).run(["K-1"], concurrency=concurrency)


def test_batch_api_and_workbench_expose_configurable_two_issue_mode(tmp_path):
    db = tmp_path / "p1.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    client = TestClient(create_p0_app(db, stage_runner=object()))

    invalid = client.post("/api/v2/issues/batch-analysis", json={"knowledge_ids": ["K-1"], "concurrency": 5})
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "BATCH_ANALYSIS_CONCURRENCY_INVALID"

    page = client.get("/p0/issues")
    assert page.status_code == 200
    assert "同时分析" in page.text and 'value="2" selected' in page.text
    assert client.get("/p0/static/p0_batch_analysis.css").status_code == 200
    javascript = client.get("/p0/static/p0_issues.js").text
    assert "/issues/batch-analysis" in javascript and "data-select-issue" in javascript
    assert "/analysis-runtime/status" in javascript and "部分完成" in javascript
    assert client.get("/api/v2/analysis-runtime/status").json()["ready"] is True


def test_batch_does_not_report_failed_or_partial_analysis_as_success():
    statuses = {"K-OK": "COMPLETED", "K-PART": "PARTIAL_FAILED", "K-FAIL": "FAILED"}

    def fake_run(_service, knowledge_id, _request):
        return SimpleNamespace(
            status=statuses[knowledge_id], analysis_set_id="AS-" + knowledge_id,
            warnings=[] if knowledge_id == "K-OK" else ["stage:failed"],
        )

    with patch("quality_knowledge.services.v2_batch_analysis_service.V2AnalysisService.run", fake_run):
        result = V2BatchAnalysisService(object(), object()).run(list(statuses), concurrency=2)

    assert result["outcome"] == "PARTIAL_FAILED"
    assert result["succeeded"] == 1 and result["partial"] == 1 and result["failed"] == 1
    assert [item["outcome"] for item in result["items"]] == ["SUCCEEDED", "PARTIAL", "FAILED"]
