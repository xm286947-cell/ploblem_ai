"""Legacy scenario RC1 rollback Gate; must not replace current issue/import/analysis."""
from fastapi.testclient import TestClient
from quality_knowledge.web.app import create_app


def test_old_scenario_and_original_workbenches_are_reachable(tmp_path):
    app = create_app(tmp_path / "quality_issue_v1.db")
    client = TestClient(app)
    expected = (
        "/quality-scenarios",
        "/quality-scenarios/generate",
        "/quality-scenarios/standardize",
        "/quality-scenarios/insights",
        "/quality-scenarios/new",
        "/quality-scenario-assets",
        "/quality-scenario-assets/portrait",
        "/issues",
        "/import",
        "/analysis",
        "/statistics",
    )
    for path in expected:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, (path, response.status_code, response.text[:400])
        assert "text/html" in response.headers["content-type"], path
    listing = client.get("/quality-scenarios").text
    assert "质量场景" in listing
    assert "quality-scenarios" in client.get("/issues").text


def test_old_scenario_lifecycle_survives_new_app_restart(tmp_path):
    database = tmp_path / "durable.db"
    app = create_app(database)
    repository = app.state.scenario_repository
    sid = repository.save_scenario(
        "",
        {"scenario_code":"RC1-KEEP-001","name":"原有场景保留验收",
         "product_code":"PLC","activity_code":"ONLINE_MONITORING","status":"DRAFT"},
        {},
    )
    reopened = create_app(database)
    assert reopened.state.scenario_repository.scenario(sid)["name"] == "原有场景保留验收"
    assert TestClient(reopened).get("/quality-scenarios").status_code == 200
