from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.overall_shell import create_overall_shell_router


def _client(*, shell_enabled=True, legacy_ready=False, legacy_scenario_ready=False):
    app = FastAPI()
    app.state.overall_shell_enabled = shell_enabled
    app.state.legacy_quality_issue_status = {
        "ready": legacy_ready,
        "code": "READY" if legacy_ready else "LEGACY_DB_UNAVAILABLE",
    }
    app.state.legacy_scenario_status = {
        "ready": legacy_scenario_ready,
        "code": "READY" if legacy_scenario_ready else "LEGACY_SCENARIO_TABLE_NOT_FOUND",
        "mode": "READ_ONLY",
    }
    app.include_router(create_overall_shell_router())
    return TestClient(app)


def test_r2_w2_four_product_areas_are_stable_shell_entries():
    client = _client()

    home = client.get("/p0/overall")
    assert home.status_code == 200
    expected = {
        "current-problem": "当前问题",
        "cases-knowledge": "案例与知识",
        "scenarios-insights": "质量场景与洞察",
        "professional-topics": "专业专题",
    }
    for area_id, title in expected.items():
        assert f'href="/p0/overall/areas/{area_id}"' in home.text
        page = client.get(f"/p0/overall/areas/{area_id}")
        assert page.status_code == 200
        assert title in page.text

    assert client.get("/p0/overall/areas/not-real").status_code == 404


def test_r2_w2_current_problem_uses_verified_w1_routes_and_does_not_fake_assessment():
    client = _client(legacy_ready=True)
    page = client.get("/p0/overall/areas/current-problem")

    assert page.status_code == 200
    for path in (
        "/p0/issues",
        "/p0/itr-recovery",
        "/p0/itr-resolution",
        "/p0/missed-test-analysis",
        "/p0/batch-analysis",
        "/analysis",
    ):
        assert f'href="{path}"' in page.text

    assert "软件问题考核" in page.text
    assert "Protected Existing Capability" in page.text
    assert "/analysis" in page.text
    assert "不由 /analysis 或材料页替代" in page.text


def test_r2_w2_old_scenario_and_portraits_bind_only_when_legacy_scenario_tables_exist():
    unavailable = _client(legacy_ready=True, legacy_scenario_ready=False)
    blocked = unavailable.get("/p0/overall/areas/scenarios-insights")
    assert blocked.status_code == 200
    for title in (
        "原有质量场景工作台",
        "原产品质量画像",
        "原客户质量画像",
        "原行业质量画像",
    ):
        assert title in blocked.text
    assert blocked.text.count("当前运行环境未绑定 Legacy 数据库") >= 4
    assert 'href="/quality-scenarios"' not in blocked.text

    client = _client(legacy_ready=True, legacy_scenario_ready=True)
    page = client.get("/p0/overall/areas/scenarios-insights")
    assert page.status_code == 200
    for path in (
        "/quality-scenarios",
        "/quality-scenario-assets",
        "/quality-scenario-assets/portrait",
        "/p0/quality-scenario-insights",
        "/p0/quality-scenario-insights?view=PRODUCT",
        "/p0/quality-scenario-insights?view=CUSTOMER",
        "/p0/quality-scenario-insights?view=INDUSTRY",
        "/p0/insights",
        "/p1/product-reports",
    ):
        assert f'href="{path}"' in page.text

    assert "Projection Parity" in page.text
    assert "不得静默退役" in page.text


def test_r2_w2_product_area_api_exposes_binding_status_without_domain_reads():
    client = _client(legacy_ready=False)
    payload = client.get("/api/v2/overall/product-areas").json()

    assert payload["total"] == 5
    by_id = {item["area_id"]: item for item in payload["items"]}

    scenario = by_id["scenarios-insights"]
    old_portrait = next(
        item for item in scenario["capabilities"]
        if item["title"] == "原产品质量画像"
    )
    assert old_portrait["available"] is False
    assert old_portrait["requires_legacy_scenario"] is True
    assert old_portrait["path"] == "/quality-scenario-assets"

    current = by_id["current-problem"]
    assessment = next(
        item for item in current["capabilities"]
        if item["title"] == "软件问题考核"
    )
    assert assessment["available"] is False
    assert assessment["binding_pending"] is True

    # Current verified R2 entries do not depend on Legacy DB readiness.
    recovery = next(
        item for item in current["capabilities"]
        if item["title"] == "ITR / 现场恢复"
    )
    assert recovery["available"] is True
