from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from quality_knowledge.web import create_app
from quality_knowledge.web.p0_pages import create_p0_insights_router


def _seed_issues(client: TestClient, tmp_path: Path) -> dict[str, str]:
    source = tmp_path / "issues.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append([
        "ITR 单号",
        "问题标题",
        "问题描述",
        "是否漏测",
        "产品",
        "平台",
        "严重程度",
    ])
    sheet.append([
        "ITR-R2-MISS-1",
        "漏测问题",
        "测试阶段未覆盖边界场景",
        "是",
        "PLC",
        "IDE",
        "A",
    ])
    sheet.append([
        "ITR-R2-NORMAL-1",
        "非漏测问题",
        "现场异常但测试已覆盖",
        "否",
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
                    "issues.xlsx",
                    stream,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"business_type": "PLC"},
        )
    assert response.status_code == 200

    items = client.get("/api/issues", params={"limit": 20}).json()["items"]
    return {item["business_issue_id"]: item["knowledge_id"] for item in items}


def test_missed_test_adapter_reuses_existing_issue_facts_only(tmp_path: Path):
    app = create_app(tmp_path / "missed-test.db")
    client = TestClient(app)
    ids = _seed_issues(client, tmp_path)

    response = client.get("/missed-test-analysis")
    assert response.status_code == 200
    assert "软件问题漏测分析" in response.text
    assert "ITR-R2-MISS-1" in response.text
    assert "ITR-R2-NORMAL-1" not in response.text
    assert "Existing Capability Adapter" in response.text
    assert "不创建第二套漏测问题对象" in response.text

    detail_url = (
        f"/issues/{ids['ITR-R2-MISS-1']}?"
        "return_to=%2Fmissed-test-analysis#causes"
    )
    assert detail_url in response.text


def test_missed_test_adapter_preserves_filter_on_issue_round_trip(tmp_path: Path):
    app = create_app(tmp_path / "missed-test-filter.db")
    client = TestClient(app)
    ids = _seed_issues(client, tmp_path)

    filtered = client.get("/missed-test-analysis", params={"q": "边界场景"})
    assert filtered.status_code == 200
    assert "ITR-R2-MISS-1" in filtered.text
    assert "return_to=%2Fmissed-test-analysis%3Fq%3D" in filtered.text
    assert "%25E8%25BE%25B9%25E7%2595%258C" in filtered.text

    detail = client.get(
        f"/issues/{ids['ITR-R2-MISS-1']}",
        params={"return_to": "/missed-test-analysis?q=边界场景"},
    )
    assert detail.status_code == 200
    assert 'data-r2-return' in detail.text
    assert 'href="/missed-test-analysis?q=边界场景"' in detail.text

    rejected = client.get(
        f"/issues/{ids['ITR-R2-MISS-1']}",
        params={"return_to": "https://example.invalid/escape"},
    )
    assert rejected.status_code == 400


def test_p0_missed_test_entry_is_a_query_preserving_compatibility_adapter():
    app = FastAPI()
    app.include_router(create_p0_insights_router())
    client = TestClient(app)

    response = client.get(
        "/p0/missed-test-analysis",
        params={"q": "ITR-1", "analysis_status": "FAILED"},
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert response.headers["location"] == (
        "/missed-test-analysis?q=ITR-1&analysis_status=FAILED"
    )
