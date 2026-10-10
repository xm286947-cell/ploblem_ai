"""Restore the original four workbench views without fabricating assessment data."""
from fastapi.testclient import TestClient
from quality_knowledge.materials import MaterialRepository
from quality_knowledge.web.app import create_app


def test_four_workbenches_and_legacy_scenarios_render(tmp_path):
    c = TestClient(create_app(tmp_path / "original_workbenches.db"))
    pages = {
        "/issues": "问题工作台",
        "/itr/recovery-workbench": "ITR / 现场恢复",
        "/itr/resolution-workbench": "ITR 彻底解决工作台",
        "/software-assessment": "软件考核工作台",
        "/missed-test-analysis": "软件问题漏测分析",
        "/materials/software-operations": "软件问题考核工作台",
        "/materials/cs": "ITR彻底解决工作台",
        "/settings/associations": "关联",
        "/quality-scenarios": "质量场景",
        "/quality-scenario-assets": "场景",
    }
    for path, expected in pages.items():
        response = c.get(path)
        assert response.status_code == 200, (path, response.status_code, response.text[:300])
        assert expected in response.text, path
    home = c.get("/issues").text
    for path in ("/materials/itr", "/materials/cs",
                 "/materials/software-operations", "/missed-test-analysis",
                 "/quality-scenarios", "/quality-scenario-assets"):
        assert f'href="{path}"' in home


def test_software_results_are_source_owned_without_score_column(tmp_path):
    db = tmp_path / "source.db"
    store = MaterialRepository(db)
    group = store.group("SW-OPS")
    store.add_material(
        group, "ITR20260424040CS",
        {"问题信息_问题描述": "现场问题", "考核信息_考核结果": "已完成整改",
         "考核信息_考核得分": 87.5, "考核信息_考核状态": "已考核"},
        "source.xlsx", "sheet1", 3)
    store.add_material(
        group, "ITR20260716098CS",
        {"问题信息_问题描述": "未提供结果的原始记录"},
        "source.xlsx", "sheet1", 4)
    page = TestClient(create_app(db)).get("/software-assessment").text
    for value in ("已完成整改", "已考核", "未提供结果的原始记录", "Source 未提供"):
        assert value in page
    # Raw imported source remains traceable; no score field is promoted into the workbench.
    assert "考核信息_考核得分" in page
    assert "87.5" in page
    assert "<th>分值</th>" not in page
    assert "assessment_score" not in page

def test_return_context_fail_closed(tmp_path):
    c = TestClient(create_app(tmp_path / "return.db"))
    r = c.get("/issues/unknown?return_to=https://evil.example", follow_redirects=False)
    assert r.status_code == 400
    assert "INVALID_ISSUE_RETURN_CONTEXT" in r.text


def test_restoration_does_not_leak_into_overall_composition(tmp_path):
    from quality_knowledge.web.app import create_legacy_quality_issue_router
    db = tmp_path / "isolated.db"
    create_app(db)
    router, state = create_legacy_quality_issue_router(db, initialize_schema=False)
    paths = {route.path for route in router.routes}
    for path in ("/itr/recovery-workbench", "/itr/resolution-workbench",
                 "/software-assessment", "/missed-test-analysis",
                 "/materials/software-operations", "/quality-scenarios"):
        assert path not in paths, path
    assert not hasattr(state, "material_repository")


def test_historical_rc1_workbench_detail_and_saved_result(tmp_path):
    db=tmp_path/"historical.db"
    first=create_app(db)
    legacy=first.state.legacy_material_repository
    key="ITR20260424040CS"
    mid,disposition=legacy.add_material(
        legacy.group("SW-OPS"),key,
        {"问题信息_彻底解决单号":key,"问题信息_问题描述":"历史软件考核原始字段",
         "数据运营_KPI计入月份":"2026-04","考核信息_考核结果":"已审核"},
        "source-history.xlsx","Sheet1",3)
    assert disposition=="NEW"
    client=TestClient(create_app(db))
    page=client.get("/materials/software-operations")
    assert page.status_code==200,page.text[:400]
    assert key in page.text
    detail=client.get("/materials/software-operations/"+mid)
    assert detail.status_code==200,detail.text[:400]
    assert "历史软件考核原始字段" in detail.text
    assert "已审核" in detail.text
