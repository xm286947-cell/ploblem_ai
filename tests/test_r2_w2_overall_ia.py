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


def test_r2_w2_current_problem_has_four_business_workbenches_and_common_problem_view():
    client = _client(legacy_ready=True)
    page = client.get("/p0/overall/areas/current-problem")

    assert page.status_code == 200
    for path in (
        "/itr/recovery-workbench",
        "/itr/resolution-workbench",
        "/software-assessment",
        "/missed-test-analysis",
    ):
        assert f'href="{path}"' in page.text

    for label in (
        "ITR工作台",
        "彻底解决工作台",
        "软件考核工作台",
        "漏测分析",
    ):
        assert label in page.text

    assert "Common Problem View · 公共问题视图" in page.text
    assert 'href="/p0/issues"' in page.text
    assert "不是第五个业务工作台" in page.text
    assert "Protected Existing Capability" not in page.text

    # Supporting capabilities remain available but are explicitly separated
    # from the four business workbenches.
    assert 'href="/p0/batch-analysis"' in page.text
    assert 'href="/analysis"' in page.text
    assert "支撑能力" in page.text
    assert 'href="/p0/issues">问题工作台</a>' not in page.text
    assert '<a class="brand" href="/p0/overall">' in page.text


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
    client = _client(legacy_ready=True)
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
    assert current["common_problem_path"] == "/p0/issues"
    assessment = next(
        item for item in current["capabilities"]
        if item["title"] == "软件考核工作台"
    )
    assert assessment["available"] is True
    assert assessment["path"] == "/software-assessment"
    assert assessment["requires_legacy"] is True
    assert assessment.get("binding_pending") is None

    # Mature Existing Capability entries are the formal routes and require the approved Legacy DB binding.
    recovery = next(
        item for item in current["capabilities"]
        if item["title"] == "ITR工作台"
    )
    assert recovery["available"] is True
    assert recovery["path"] == "/itr/recovery-workbench"
    assert recovery["requires_legacy"] is True
