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
    client = TestClient(app)

    # Same FastAPI/TestClient serves the mature platform and additive QSV1 UI.
    assert client.get("/").status_code in {200, 307}
    assert client.get("/issues").status_code == 200
    assert client.get("/analysis").status_code == 200
    assert client.get("/import").status_code == 200
    assert client.get("/p0/quality-scenarios/workbench").status_code == 200
    assert client.get("/p0/quality-scenarios").status_code == 200
    assert client.get("/p0/quality-scenario-insights").status_code == 200

    # STEP1B owns only the /p0/ QSV1 namespace. If a legacy scenario route is
    # present on a later mature baseline, it must not render the V1 preview UI.
    legacy = client.get("/quality-scenarios")
    if legacy.status_code == 200:
        assert "新质量场景库（预览）" not in legacy.text
        assert "QualityScenario V1" not in legacy.text


def test_mature_qsv1_shell_returns_to_mature_routes_without_p0_duplicates(tmp_path, monkeypatch):
    monkeypatch.delenv("QUALITY_SCENARIO_V1_DB_PATH", raising=False)
    client = TestClient(create_app(tmp_path / "quality_issue_v1.db"))

    for path in (
        "/p0/quality-scenarios/workbench",
        "/p0/quality-scenarios",
        "/p0/quality-scenario-insights",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        for mature_path in (
            "/issues",
            "/analysis",
            "/itr/recovery-workbench",
            "/itr/resolution-workbench",
            "/software-assessment",
            "/missed-test-analysis",
            "/product-reports",
            "/import",
            "/quality-scenarios",
            "/quality-scenario-assets",
            "/quality-scenarios/insights",
            "/settings/scenario-taxonomy",
        ):
            assert f'href="{mature_path}"' in response.text
        for duplicate_path in (
            "/p0/itr-recovery",
            "/p0/itr-resolution",
            "/p0/software-assessment",
            "/p0/missed-test-analysis",
        ):
            assert f'href="{duplicate_path}"' not in response.text

    for path in (
        "/issues",
        "/analysis",
        "/import",
        "/quality-scenarios",
        "/quality-scenario-assets",
        "/quality-scenario-assets/portrait",
        "/quality-scenarios/insights",
        "/settings/scenario-taxonomy",
    ):
        assert client.get(path).status_code == 200, path


def test_mature_qsv1_local_nav_returns_to_reachable_mature_host(tmp_path, monkeypatch):
    monkeypatch.delenv("QUALITY_SCENARIO_V1_DB_PATH", raising=False)
    client = TestClient(create_app(tmp_path / "quality_issue_v1.db"))

    response = client.get("/p0/quality-scenario-insights")
    assert response.status_code == 200
    assert 'href="/issues">返回问题工作台</a>' in response.text
    assert 'href="/p0/overall">返回总体工作台</a>' not in response.text
