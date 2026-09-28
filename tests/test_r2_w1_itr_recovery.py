from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web import create_app
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _seed_itr(client: TestClient, tmp_path: Path) -> str:
    source = tmp_path / "itr-recovery.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append([
        "ITR 单号",
        "问题标题",
        "问题描述",
        "产品",
        "平台",
        "严重程度",
        "发生阶段",
        "现场作业记录",
        "临时措施",
    ])
    sheet.append([
        "ITR-R2-RECOVERY-1",
        "现场通信恢复",
        "客户现场通信异常",
        "PLC",
        "IDE",
        "A",
        "客户现场运行",
        "重启通信服务并恢复现场业务",
        "切换备用链路维持生产",
    ])
    book.save(source)

    with source.open("rb") as stream:
        response = client.post(
            "/api/issues/import",
            files={
                "file": (
                    "itr-recovery.xlsx",
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
        if item["business_issue_id"] == "ITR-R2-RECOVERY-1"
    )


def test_itr_recovery_is_read_only_projection_of_raw_source_facts(tmp_path: Path):
    app = create_app(tmp_path / "itr-recovery.db")
    client = TestClient(app)
    knowledge_id = _seed_itr(client, tmp_path)

    page = client.get("/itr/recovery-workbench")
    assert page.status_code == 200
    assert "ITR / 现场恢复" in page.text
    assert "READ_ONLY_SOURCE_OWNED" in page.text
    assert "NO_SILENT_OVERWRITE" in page.text
    assert "ITR-R2-RECOVERY-1" in page.text
    assert "客户现场运行" in page.text
    assert "重启通信服务并恢复现场业务" in page.text
    assert "切换备用链路维持生产" in page.text
    assert "itr-recovery.xlsx" in page.text
    assert "Row 2" in page.text

    filtered = client.get("/itr/recovery-workbench", params={"q": "备用链路"})
    assert filtered.status_code == 200
    assert "ITR-R2-RECOVERY-1" in filtered.text
    assert "return_to=%2Fitr%2Frecovery-workbench%3Fq%3D" in filtered.text

    detail = client.get(
        f"/issues/{knowledge_id}",
        params={"return_to": "/itr/recovery-workbench?q=备用链路"},
    )
    assert detail.status_code == 200
    assert "返回 ITR / 现场恢复" in detail.text
    assert 'href="/itr/recovery-workbench?q=备用链路"' in detail.text

    rejected = client.get(
        f"/issues/{knowledge_id}",
        params={"return_to": "https://example.invalid/escape"},
    )
    assert rejected.status_code == 400

    # R2 owns only the aligned consumption view; source-system write actions
    # are deliberately absent.
    assert client.post("/itr/recovery-workbench").status_code == 405


def test_p0_itr_recovery_is_real_shell_page_and_preserves_return_context(tmp_path: Path):
    legacy = create_app(tmp_path / "p0-itr-recovery.db")
    legacy_client = TestClient(legacy)
    knowledge_id = _seed_itr(legacy_client, tmp_path)

    p0_db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)

    app = create_p0_app(
        p0_db,
        stage_runner=object(),
        project_root=ROOT,
        legacy_quality_issue_db_path=tmp_path / "p0-itr-recovery.db",
    )
    client = TestClient(app)

    page = client.get("/p0/itr-recovery", params={"q": "通信服务"})
    assert page.status_code == 200
    assert "ALIGNED CONSUMPTION VIEW" in page.text
    assert "ITR-R2-RECOVERY-1" in page.text
    assert "重启通信服务并恢复现场业务" in page.text
    assert "return_to=%2Fp0%2Fitr-recovery%3Fq%3D" in page.text

    detail = client.get(
        f"/p0/issues/{knowledge_id}",
        params={"return_to": "/p0/itr-recovery?q=通信服务"},
    )
    assert detail.status_code == 200
    assert "返回 ITR / 现场恢复" in detail.text
    assert 'href="/p0/itr-recovery?q=通信服务"' in detail.text

    rejected = client.get(
        f"/p0/issues/{knowledge_id}",
        params={"return_to": "https://example.invalid/escape"},
    )
    assert rejected.status_code == 400
