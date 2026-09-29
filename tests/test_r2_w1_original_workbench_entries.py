from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from openpyxl import Workbook

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web import create_app
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _seed_issue(client: TestClient, tmp_path: Path) -> str:
    source = tmp_path / "software-assessment-issue.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append([
        "ITR 单号",
        "问题标题",
        "问题描述",
        "产品",
        "平台",
        "严重程度",
    ])
    sheet.append([
        "ITR-R2-SW-1",
        "软件异常",
        "软件问题用于考核关联",
        "PLC",
        "IDE",
        "B",
    ])
    book.save(source)
    with source.open("rb") as stream:
        response = client.post(
            "/api/issues/import",
            files={
                "file": (
                    "software-assessment-issue.xlsx",
                    stream,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"business_type": "PLC"},
        )
    assert response.status_code == 200
    items = client.get("/api/issues", params={"limit": 20}).json()["items"]
    return next(
        item["knowledge_id"]
        for item in items
        if item["business_issue_id"] == "ITR-R2-SW-1"
    )


def _software_assessment_source(path: Path) -> None:
    book = Workbook()
    sheet = book.active
    sheet.append([
        "问题信息",
        "问题信息",
        "考核信息",
        "考核信息",
        "考核信息",
        "责任信息",
        "责任信息",
    ])
    sheet.append([
        "彻底解决单号",
        "问题描述",
        "考核状态",
        "考核结果",
        "扣分",
        "责任部门",
        "责任人",
    ])
    sheet.append([
        "ITR-R2-SW-1CS",
        "软件问题考核记录",
        "已考核",
        "责任确认",
        "2",
        "软件研发部",
        "研发B",
    ])
    book.save(path)


def _import_software_assessment(client: TestClient, source: Path) -> None:
    with source.open("rb") as stream:
        response = client.post(
            "/materials/import",
            data={
                "workbench": "software-operations",
                "group_code": "SW-OPS",
                "header_rows": "2",
            },
            files={
                "file": (
                    "software-assessment.xlsx",
                    stream,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert response.status_code == 200


def test_original_current_problem_entries_are_restored_in_legacy_shell(tmp_path: Path):
    client = TestClient(create_app(tmp_path / "legacy-nav.db"))
    page = client.get("/issues")
    assert page.status_code == 200
    expected = {
        "/itr/recovery-workbench": "ITR工作台",
        "/itr/resolution-workbench": "彻底解决工作台",
        "/software-assessment": "软件考核工作台",
        "/missed-test-analysis": "漏测分析",
    }
    for href, label in expected.items():
        assert f'href="{href}"' in page.text
        assert label in page.text


def test_software_assessment_entry_projects_source_facts_without_new_state_machine(tmp_path: Path):
    db = tmp_path / "software-assessment.db"
    client = TestClient(create_app(db))
    knowledge_id = _seed_issue(client, tmp_path)

    source = tmp_path / "software-assessment.xlsx"
    _software_assessment_source(source)
    _import_software_assessment(client, source)

    page = client.get("/software-assessment")
    assert page.status_code == 200
    assert "软件考核工作台" in page.text
    assert "ENTRY_RESTORED" in page.text
    assert "SOURCE_ACTION_BINDING_PENDING" in page.text
    assert "NO_SECOND_ASSESSMENT_STATE_MACHINE" in page.text
    assert "ITR-R2-SW-1CS" in page.text
    assert "软件问题考核记录" in page.text
    assert "已考核" in page.text
    assert "责任确认" in page.text
    assert "软件研发部" in page.text
    assert "研发B" in page.text
    assert ">2<" in page.text

    filtered = client.get("/software-assessment", params={"q": "软件研发部"})
    assert filtered.status_code == 200
    assert "ITR-R2-SW-1CS" in filtered.text
    assert "return_to=%2Fsoftware-assessment%3Fq%3D" in filtered.text

    detail = client.get(
        f"/issues/{knowledge_id}",
        params={"return_to": "/software-assessment?q=软件研发部"},
    )
    assert detail.status_code == 200
    assert "返回软件考核工作台" in detail.text
    assert 'href="/software-assessment?q=软件研发部"' in detail.text

    rejected = client.get(
        f"/issues/{knowledge_id}",
        params={"return_to": "https://example.invalid/escape"},
    )
    assert rejected.status_code == 400

    assert client.post("/software-assessment").status_code == 405


def test_original_current_problem_entries_are_restored_in_p0_shell(tmp_path: Path):
    legacy_db = tmp_path / "p0-software-assessment-legacy.db"
    legacy_client = TestClient(create_app(legacy_db))
    knowledge_id = _seed_issue(legacy_client, tmp_path)

    source = tmp_path / "software-assessment.xlsx"
    _software_assessment_source(source)
    _import_software_assessment(legacy_client, source)

    # Simulate a historical database whose source facts survived but whose
    # association-link projection was not rebuilt during R2 assembly.
    with sqlite3.connect(legacy_db) as connection:
        connection.execute("DELETE FROM issue_material_link")
        assert connection.execute("SELECT COUNT(*) FROM issue_material_link").fetchone()[0] == 0

    p0_db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)

    app = create_p0_app(
        p0_db,
        stage_runner=object(),
        project_root=ROOT,
        legacy_quality_issue_db_path=legacy_db,
    )
    client = TestClient(app)

    issues = client.get("/p0/issues")
    assert issues.status_code == 200
    expected = {
        "/p0/itr-recovery": "ITR工作台",
        "/p0/itr-resolution": "彻底解决工作台",
        "/p0/software-assessment": "软件考核工作台",
        "/p0/missed-test-analysis": "漏测分析",
    }
    for href, label in expected.items():
        assert f'href="{href}"' in issues.text
        assert label in issues.text

    page = client.get("/p0/software-assessment", params={"q": "责任确认"})
    assert page.status_code == 200
    assert "EXISTING CAPABILITY MOUNT" in page.text
    assert "ITR-R2-SW-1CS" in page.text
    assert "责任确认" in page.text
    assert "return_to=%2Fp0%2Fsoftware-assessment%3Fq%3D" in page.text

    detail = client.get(
        f"/p0/issues/{knowledge_id}",
        params={"return_to": "/p0/software-assessment?q=责任确认"},
    )
    assert detail.status_code == 200
    assert 'href="/p0/software-assessment?q=责任确认"' in detail.text
    assert "返回软件考核工作台" in detail.text
    assert "关联业务对象" in detail.text
    assert "/p0/software-assessment?q=ITR-R2-SW-1" in detail.text
    assert "CANONICAL_PROBLEM_IDENTITY" in detail.text
    assert "未发现已确认关联" in detail.text

    static_js = client.get("/p0/static/p0_issue_detail.js")
    assert static_js.status_code == 200
    assert "returnTo === '/p0/software-assessment'" in static_js.text

    # Read-time recovery must remain read-only; it may project the unique
    # canonical relation but must not silently repopulate the Legacy link table.
    with sqlite3.connect(legacy_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM issue_material_link").fetchone()[0] == 0

    assert client.post("/p0/software-assessment").status_code == 405
