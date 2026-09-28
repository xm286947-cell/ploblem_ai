from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web import create_app
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _issue_book(path: Path) -> None:
    book = Workbook()
    sheet = book.active
    sheet.append([
        "ITR 单号",
        "问题描述",
        "问题原因定位（×开发填写×）",
        "问题解决方案（×开发填写×）",
        "是否漏测",
        "产品",
        "平台",
        "严重程度",
    ])
    sheet.append([
        "ITR-R2-MISS-1",
        "通信异常未在测试阶段发现",
        "边界条件遗漏",
        "修复边界处理",
        "是",
        "PLC",
        "IDE",
        "A",
    ])
    sheet.append([
        "ITR-R2-NORMAL-1",
        "普通软件问题",
        "参数校验遗漏",
        "补充校验",
        "否",
        "PLC",
        "IDE",
        "B",
    ])
    book.save(path)


def _seed_legacy(legacy_db: Path, tmp_path: Path):
    app = create_app(legacy_db)
    client = TestClient(app)
    source = tmp_path / "issues.xlsx"
    _issue_book(source)
    with source.open("rb") as stream:
        response = client.post(
            "/api/issues/import",
            files={
                "file": (
                    "issues.xlsx",
                    stream,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"business_type": "PLC"},
        )
    assert response.status_code == 200
    items = client.get("/api/issues", params={"limit": 20}).json()["items"]
    ids = {item["business_issue_id"]: item["knowledge_id"] for item in items}
    return app, client, ids


def test_missed_test_adapter_uses_existing_source_fact_only(tmp_path: Path):
    legacy_db = tmp_path / "legacy.db"
    _, client, ids = _seed_legacy(legacy_db, tmp_path)

    page = client.get("/missed-test-analysis")
    assert page.status_code == 200
    assert "软件问题漏测分析" in page.text
    assert "ITR-R2-MISS-1" in page.text
    assert "ITR-R2-NORMAL-1" not in page.text
    assert "不创建第二套漏测问题" in page.text

    filtered = client.get(
        "/missed-test-analysis",
        params={"q": "MISS-1", "analysis_status": "NOT_ANALYZED"},
    )
    assert filtered.status_code == 200
    expected_return = "%2Fmissed-test-analysis%3Fq%3DMISS-1%26analysis_status%3DNOT_ANALYZED"
    assert (
        f"/issues/{ids['ITR-R2-MISS-1']}?return_to={expected_return}#causes"
        in filtered.text
    )

    detail = client.get(
        f"/issues/{ids['ITR-R2-MISS-1']}",
        params={"return_to": "/missed-test-analysis?q=MISS-1&analysis_status=NOT_ANALYZED"},
    )
    assert detail.status_code == 200
    assert "data-r2-return" in detail.text

    rejected = client.get(
        f"/issues/{ids['ITR-R2-MISS-1']}",
        params={"return_to": "https://example.invalid/escape"},
    )
    assert rejected.status_code == 400


def test_p0_missed_test_workbench_reuses_legacy_sot_and_preserves_return(tmp_path: Path):
    legacy_db = tmp_path / "legacy.db"
    _, _, ids = _seed_legacy(legacy_db, tmp_path)

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

    page = client.get(
        "/p0/missed-test-analysis",
        params={"q": "MISS-1", "analysis_status": "NOT_ANALYZED"},
    )
    assert page.status_code == 200
    assert "软件问题漏测分析" in page.text
    assert "ITR-R2-MISS-1" in page.text
    assert "ITR-R2-NORMAL-1" not in page.text
    expected_return = "%2Fp0%2Fmissed-test-analysis%3Fq%3DMISS-1%26analysis_status%3DNOT_ANALYZED"
    assert (
        f"/p0/issues/{ids['ITR-R2-MISS-1']}?return_to={expected_return}#analysis"
        in page.text
    )

    detail = client.get(
        f"/p0/issues/{ids['ITR-R2-MISS-1']}",
        params={"return_to": "/p0/missed-test-analysis?q=MISS-1&analysis_status=NOT_ANALYZED"},
    )
    assert detail.status_code == 200
    assert "返回漏测分析" in detail.text
    assert "/p0/missed-test-analysis?q=MISS-1" in detail.text

    rejected = client.get(
        f"/p0/issues/{ids['ITR-R2-MISS-1']}",
        params={"return_to": "https://example.invalid/escape"},
    )
    assert rejected.status_code == 400

    issues = client.get("/p0/issues")
    assert issues.status_code == 200
    assert 'href="/p0/missed-test-analysis"' in issues.text
    assert 'href="/itr/resolution-workbench"' in issues.text
