from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.overall_shell import create_overall_shell_router


def _client(*, legacy_ready: bool = True) -> TestClient:
    app = FastAPI()
    app.state.overall_shell_enabled = True
    app.state.storage_workspace_binding = None
    app.state.legacy_quality_issue_status = {
        "ready": legacy_ready,
        "code": "READY" if legacy_ready else "LEGACY_DB_PATH_NOT_CONFIGURED",
    }
    app.include_router(create_overall_shell_router())
    return TestClient(app)


def test_formal_product_ia_has_exactly_four_business_workspaces():
    client = _client()

    payload = client.get("/api/v2/overall/product-areas").json()
    assert payload["total"] == 4
    assert [item["area_id"] for item in payload["items"]] == [
        "current-problem",
        "cases-knowledge",
        "scenarios-insights",
        "professional-topics",
    ]
    assert [item["title"] for item in payload["items"]] == [
        "当前问题",
        "案例与知识",
        "质量场景与洞察",
        "专业专题",
    ]

    page = client.get("/p0/overall")
    assert page.status_code == 200
    assert "四大 Workspace" in page.text
    assert "产品域兼容入口" in page.text
    for area_id in (
        "current-problem",
        "cases-knowledge",
        "scenarios-insights",
        "professional-topics",
    ):
        assert f'/p0/overall/areas/{area_id}' in page.text


def test_current_problem_workspace_uses_w1_real_routes_and_keeps_unknown_assessment_pending():
    client = _client()

    page = client.get("/p0/overall/areas/current-problem")
    assert page.status_code == 200
    for route in (
        "/p0/issues",
        "/p0/itr-recovery",
        "/p0/itr-resolution",
        "/p0/missed-test-analysis",
        "/p0/batch-analysis",
        "/p0/cases",
        "/analysis",
    ):
        assert f'href="{route}"' in page.text

    assert "软件问题考核" in page.text
    assert "能力保留 · Route / State Binding Pending" in page.text
    assert "跨 Workspace 旅程入口 · 正式归属不变" in page.text


def test_old_scenario_and_portrait_capabilities_remain_visible_until_projection_parity():
    client = _client()

    page = client.get("/p0/overall/areas/scenarios-insights")
    assert page.status_code == 200

    for capability in (
        "原有质量场景工作台",
        "原产品质量画像",
        "原客户质量画像",
        "原行业质量画像",
    ):
        assert capability in page.text

    assert page.text.count("KEEP_UNTIL_PROJECTION_PARITY · Route Binding Pending") == 4
    assert "Route 未核实不等于 Capability 不存在" in page.text

    for route in (
        "/p0/quality-scenario-insights",
        "/p0/quality-scenario-insights?view=PRODUCT",
        "/p0/quality-scenario-insights?view=CUSTOMER",
        "/p0/quality-scenario-insights?view=INDUSTRY",
        "/p0/insights",
        "/p1/product-reports",
    ):
        assert f'href="{route.replace("&", "&amp;")}"' in page.text


def test_existing_domain_workspace_registry_remains_backward_compatible():
    client = _client()

    payload = client.get("/api/v2/overall/workspaces").json()
    assert payload["total"] == 4
    expected = {
        "major": "/p0/cases",
        "quality-scenario": "/p0/quality-scenario-insights",
        "hardware": "/p0/hardware-cases",
        "storage": "/storage-workspace/",
    }
    assert {item["workspace_id"]: item["entry_path"] for item in payload["items"]} == expected

    for workspace_id, target in expected.items():
        response = client.get(f"/p0/workspaces/{workspace_id}", follow_redirects=False)
        assert response.status_code in {302, 307}
        assert response.headers["location"] == target


def test_management_is_secondary_not_a_fifth_business_workspace():
    client = _client()

    formal = client.get("/api/v2/overall/product-areas").json()
    assert all(item["area_id"] != "management" for item in formal["items"])

    management = client.get("/p0/overall/areas/management")
    assert management.status_code == 200
    assert "管理与配置" in management.text
    assert 'href="/p0/data-intake"' in management.text
    assert 'href="/materials/itr"' in management.text
