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
        "/materials/software-operations": "软件运营数据导入",
        "/materials/cs": "彻底解决单材料导入",
        "/settings/associations": "关联",
        "/quality-scenarios": "质量场景",
        "/quality-scenario-assets": "场景",
    }
    for path, expected in pages.items():
        response = c.get(path)
        assert response.status_code == 200, (path, response.status_code, response.text[:300])
        assert expected in response.text, path
    home = c.get("/issues").text
    for path in ("/itr/recovery-workbench", "/itr/resolution-workbench",
                 "/software-assessment", "/missed-test-analysis",
                 "/quality-scenarios", "/quality-scenario-assets"):
        assert f'href="{path}"' in home


def test_software_score_and_result_are_source_owned(tmp_path):
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
        {"问题信息_问题描述": "尚无考核分值"},
        "source.xlsx", "sheet1", 4)
    page = TestClient(create_app(db)).get("/software-assessment").text
    for value in ("已完成整改", "87.5", "已考核", "尚无考核分值", "Source 未提供"):
        assert value in page
    assert "87.5" in TestClient(create_app(db)).get("/software-assessment").text


def test_return_context_fail_closed(tmp_path):
    c = TestClient(create_app(tmp_path / "return.db"))
    r = c.get("/issues/unknown?return_to=https://evil.example", follow_redirects=False)
    assert r.status_code == 400
    assert "INVALID_ISSUE_RETURN_CONTEXT" in r.text
