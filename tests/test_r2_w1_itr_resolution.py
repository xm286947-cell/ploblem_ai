# R2 W1 focused regression: ITR resolution vertical slice.
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from quality_knowledge.web import create_app


def _seed_issue(client: TestClient, tmp_path: Path) -> str:
    source = tmp_path / "issue.xlsx"
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
        "ITR-R2-W1-1",
        "现场通信异常",
        "边界处理遗漏",
        "补充边界处理",
        "是",
        "PLC",
        "IDE",
        "A",
    ])
    book.save(source)
    with source.open("rb") as stream:
        response = client.post(
            "/api/issues/import",
            files={
                "file": (
                    "issue.xlsx",
                    stream,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"business_type": "PLC"},
        )
    assert response.status_code == 200
    return client.get("/api/issues").json()["items"][0]["knowledge_id"]


def _resolution_source(path: Path) -> None:
    book = Workbook()
    sheet = book.active
    sheet.append(["问题信息", "问题信息", "问题信息"])
    sheet.append(["彻底解决单号", "产品类型", "问题描述"])
    sheet.append(["ITR-R2-W1-1CS", "PLC", "现场通信异常彻底解决记录"])
    book.save(path)


def test_r2_w1_resolution_workbench_is_source_linked_and_round_trips(tmp_path: Path):
    app = create_app(tmp_path / "r2-w1.db")
    client = TestClient(app)
    knowledge_id = _seed_issue(client, tmp_path)

    issue_page = client.get(f"/issues/{knowledge_id}")
    assert issue_page.status_code == 200
    assert 'href="/itr/resolution-workbench"' in issue_page.text

    list_page = client.get("/issues")
    assert list_page.status_code == 200
    assert 'href="/itr/resolution-workbench"' in list_page.text

    source = tmp_path / "resolution.xlsx"
    _resolution_source(source)
    with source.open("rb") as stream:
        imported = client.post(
            "/materials/import",
            data={
                "workbench": "cs",
                "group_code": "ITR-CS",
                "header_rows": "2",
            },
            files={
                "file": (
                    "resolution.xlsx",
                    stream,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert imported.status_code == 200

    workbench = client.get("/itr/resolution-workbench")
    assert workbench.status_code == 200
    assert "ITR 彻底解决工作台" in workbench.text
    assert "ITR-R2-W1-1CS" in workbench.text
    assert "ITR-R2-W1-1" in workbench.text
    expected = f'/issues/{knowledge_id}?return_to=%2Fitr%2Fresolution-workbench'
    assert expected in workbench.text

    linked_issue = client.get(
        f"/issues/{knowledge_id}",
        params={"return_to": "/itr/resolution-workbench"},
    )
    assert linked_issue.status_code == 200
    assert 'data-r2-return' in linked_issue.text
    assert 'href="/itr/resolution-workbench"' in linked_issue.text

    rejected = client.get(
        f"/issues/{knowledge_id}",
        params={"return_to": "https://example.invalid/escape"},
    )
    assert rejected.status_code == 400


def test_r2_w1_resolution_workbench_does_not_guess_unlinked_itr(tmp_path: Path):
    app = create_app(tmp_path / "r2-w1-unlinked.db")
    client = TestClient(app)

    source = tmp_path / "unlinked.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["问题信息", "问题信息"])
    sheet.append(["彻底解决单号", "问题描述"])
    sheet.append(["ITR-NOT-FOUND-CS", "无匹配 ITR"])
    book.save(source)

    with source.open("rb") as stream:
        imported = client.post(
            "/materials/import",
            data={"workbench": "cs", "group_code": "ITR-CS", "header_rows": "2"},
            files={
                "file": (
                    "unlinked.xlsx",
                    stream,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert imported.status_code == 200

    workbench = client.get("/itr/resolution-workbench")
    assert workbench.status_code == 200
    assert "未匹配" in workbench.text
    assert "?return_to=%2Fitr%2Fresolution-workbench" not in workbench.text
