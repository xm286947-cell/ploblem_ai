from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.web.app import create_app


def test_step1b_qsv1_preview_is_mounted_in_mature_host(tmp_path, monkeypatch):
    legacy_db = tmp_path / "quality_issue_v1.db"
    qsv1_db = tmp_path / "quality_scenario_v1_preview.db"
    monkeypatch.setenv("QUALITY_SCENARIO_V1_DB_PATH", str(qsv1_db))

    app = create_app(legacy_db)
    client = TestClient(app)

    for path in ("/issues", "/analysis", "/import"):
        response = client.get(path)
        assert response.status_code == 200, path

    issues = client.get("/issues")
    assert "新质量场景（预览）" in issues.text
    assert 'href="/p0/quality-scenarios/workbench"' in issues.text

    for path in (
        "/p0/quality-scenarios/workbench",
        "/p0/quality-scenarios",
        "/p0/quality-scenario-insights",
    ):
        response = client.get(path)
        assert response.status_code == 200, path

    workflow = client.get(
        "/api/v2/quality-scenario-workflow/v1/quality-scenarios"
    )
    assert workflow.status_code == 200
    assert workflow.json() == {"items": [], "total": 0}

    status = client.get("/api/v2/quality-scenario-preview/status")
    assert status.status_code == 200
    payload = status.json()
    assert payload["V1_DB_ABSOLUTE_PATH"] == str(qsv1_db.resolve())
    assert payload["V1_CANDIDATE_COUNT"] == 0
    assert payload["V1_PUBLISHED_COUNT"] == 0
    assert payload["P04_PROVIDER_TYPE"] == "QualityScenarioV1P04Provider"
    assert payload["P04_PUBLISHED_COUNT"] == 0
    assert payload["SYNTHETIC_FIXTURE_USED"] == "NO"

    selectors = client.get(
        "/api/v2/quality-scenario-insights/v1/selectors",
        params={"view": "PRODUCT"},
    )
    assert selectors.status_code == 200
    assert selectors.json()["state"] == "EMPTY"


def test_step1b_preview_does_not_create_second_host_or_overwrite_legacy_route(tmp_path, monkeypatch):
    monkeypatch.delenv("QUALITY_SCENARIO_V1_DB_PATH", raising=False)
    db = tmp_path / "quality_issue_v1.db"
    app = create_app(db)
    paths = {route.path for route in app.routes}

    assert "/" in paths
    assert "/issues" in paths
    assert "/analysis" in paths
    assert "/import" in paths
    assert "/p0/quality-scenarios/workbench" in paths
    assert "/p0/quality-scenarios" in paths
    assert "/p0/quality-scenarios/library/{scenario_id}" in paths
    assert "/p0/quality-scenario-insights" in paths
    assert "/api/v2/quality-scenario-preview/status" in paths

    # STEP1B owns only the /p0/ QualityScenario V1 namespace. Existing
    # /quality-scenarios routes, when present on a later mature baseline,
    # remain outside this integration and must not be overwritten.
    assert "/p0/quality-scenarios" in paths
