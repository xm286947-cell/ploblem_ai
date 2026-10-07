from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "tests/golden/hardware_case_scenarios/A9001-LDO 输出振荡.docx"
GROUP = "DA-MP-02-CANONICAL-INTAKE"


def _client(tmp_path: Path) -> TestClient:
    p0_db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    return TestClient(create_p0_app(
        p0_db,
        project_root=ROOT,
        major_case_db_path=tmp_path / "major.db",
        major_attachment_root=tmp_path / "attachments",
        major_artifact_root=tmp_path / "artifacts",
    ))


def _excel_bytes(*, igr: str, itr: str, description: str = "Canonical intake convergence") -> bytes:
    output = BytesIO()
    book = Workbook()
    sheet = book.active
    sheet.title = "重大问题"
    sheet.append([
        "IGR编号", "ITR单号", "问题描述", "TRC发生", "MRC发生", "产品", "模块",
        "Failure Mechanism", "Trigger Condition",
    ])
    sheet.append([
        igr, itr, description, "恢复过程异常", "复核遗漏", "PLC-X", "控制器",
        "配置更新中断", "掉电恢复窗口",
    ])
    book.save(output)
    book.close()
    return output.getvalue()


def _import_excel(client: TestClient, *, igr: str, itr: str, file_name: str = "major.xlsx") -> dict:
    preview = client.post(
        "/api/v2/major-production/excel/preview",
        data={"group_code": GROUP, "domain": "PLC"},
        files={"file": (
            file_name,
            _excel_bytes(igr=igr, itr=itr),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )},
    )
    assert preview.status_code == 200, preview.text
    confirmed = client.post(
        "/api/v2/major-production/excel/confirm",
        data={"batch_id": preview.json()["batch_id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["result"]


def _intake_document(client: TestClient, *, itr: str, file_name: str = "report.docx") -> dict:
    response = client.post(
        "/api/v2/major-production/sources",
        data={
            "title": "Canonical intake convergence",
            "group_code": GROUP,
            "domain": "PLC",
            "standard_itr": itr,
        },
        files={"file": (
            file_name,
            DOCUMENT.read_bytes(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _case_snapshot(client: TestClient) -> list[dict]:
    return client.app.state.major_case_repository.list_cases(group_code=GROUP)["items"]


def _counts(client: TestClient) -> dict[str, int]:
    repository = client.app.state.major_case_repository
    with repository.connect() as connection:
        return {
            "cases": connection.execute(
                "SELECT COUNT(*) FROM kb_case WHERE group_code=?", (GROUP,)
            ).fetchone()[0],
            "events": connection.execute(
                "SELECT COUNT(*) FROM kb_event WHERE group_code=?", (GROUP,)
            ).fetchone()[0],
            "source_facts": connection.execute(
                """SELECT COUNT(*) FROM kb_source_fact_revision sf
                   JOIN kb_case c ON c.case_id=sf.case_id WHERE c.group_code=?""",
                (GROUP,),
            ).fetchone()[0],
            "documents": connection.execute(
                """SELECT COUNT(*) FROM kb_case_document cd
                   JOIN kb_case c ON c.case_id=cd.case_id WHERE c.group_code=?""",
                (GROUP,),
            ).fetchone()[0],
            "source_links": connection.execute(
                """SELECT COUNT(*) FROM kb_source_link sl
                   JOIN kb_case c ON c.case_id=sl.case_id WHERE c.group_code=?""",
                (GROUP,),
            ).fetchone()[0],
            "identities": connection.execute(
                "SELECT COUNT(*) FROM kb_case_identity WHERE group_code=?", (GROUP,)
            ).fetchone()[0],
        }


def test_excel_then_document_same_itr_converges(tmp_path: Path) -> None:
    client = _client(tmp_path)
    itr = "ITR-506-EXCEL-DOCUMENT"

    excel_result = _import_excel(client, igr="IGR-506-EXCEL-DOCUMENT", itr=itr)
    excel_case_id = excel_result["case_ids"][0]
    document_result = _intake_document(client, itr=itr)

    cases = _case_snapshot(client)
    repository = client.app.state.major_case_repository
    detail = repository.case_detail(excel_case_id)
    with repository.connect() as connection:
        source_fact_count = connection.execute(
            "SELECT COUNT(*) FROM kb_source_fact_revision WHERE case_id=? AND source_type='EXCEL'",
            (excel_case_id,),
        ).fetchone()[0]
    assert len(cases) == 1
    assert document_result["case"]["case_id"] == excel_case_id
    assert document_result["event"]["event_id"] == detail["events"][0]["event_id"]
    assert document_result["source_link"]["event_id"] == detail["events"][0]["event_id"]
    assert len(detail["events"]) == 1
    assert source_fact_count == 1
    assert any(link["source_type"] == "MAJOR_SOURCE_DOCUMENT" for link in detail["source_links"])
    assert any(link["source_type"] == "MAJOR_EXCEL_SOURCE_FACT" for link in detail["source_links"])


def test_document_then_excel_same_itr_converges(tmp_path: Path) -> None:
    client = _client(tmp_path)
    itr = "ITR-506-DOCUMENT-EXCEL"

    document_result = _intake_document(client, itr=itr)
    excel_result = _import_excel(client, igr="", itr=itr)

    cases = _case_snapshot(client)
    detail = client.app.state.major_case_repository.case_detail(document_result["case"]["case_id"])
    assert len(cases) == 1
    assert excel_result["case_ids"] == [document_result["case"]["case_id"]]
    assert len(detail["events"]) == 1
    assert excel_result["events"] == 1
    assert any(link["source_type"] == "MAJOR_SOURCE_DOCUMENT" for link in detail["source_links"])
    assert any(link["source_type"] == "MAJOR_EXCEL_SOURCE_FACT" for link in detail["source_links"])
    assert _counts(client)["source_facts"] == 1


def test_document_then_excel_igr_itr_alias_converges(tmp_path: Path) -> None:
    client = _client(tmp_path)
    itr = "ITR-506-IGR-ALIAS"
    igr = "IGR-506-IGR-ALIAS"

    document_result = _intake_document(client, itr=itr)
    original_event_id = document_result["event"]["event_id"]
    excel_result = _import_excel(client, igr=igr, itr=itr)

    case_id = document_result["case"]["case_id"]
    assert len(_case_snapshot(client)) == 1
    assert excel_result["case_ids"] == [case_id]
    assert client.app.state.major_case_repository.events(case_id)[0]["event_id"] == original_event_id
    identities = client.app.state.major_case_restore_service.identities(case_id)
    assert any(
        identity["identity_type"] == "IGR" and identity["identity_value"] == igr
        for identity in identities
    )
    detail = client.app.state.major_case_repository.case_detail(case_id)
    assert any(link["source_type"] == "MAJOR_SOURCE_DOCUMENT" for link in detail["source_links"])
    assert _counts(client)["source_facts"] == 1


def test_major_identity_conflict_fails_closed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    repository = client.app.state.major_case_repository
    restore = client.app.state.major_case_restore_service
    itr = "ITR-506-CONFLICT"
    igr = "IGR-506-CONFLICT"

    case_by_igr = repository.create_case("IGR owner", GROUP, domain="PLC")
    case_by_itr = repository.create_case("ITR owner", GROUP, domain="PLC")
    restore._set_identity(case_by_igr["case_id"], GROUP, "IGR", igr, primary=True)
    existing_event = repository.upsert_event(
        case_by_itr["case_id"], standard_itr=itr, internal_event_key=itr, title=itr
    )
    before = _counts(client)

    preview = client.post(
        "/api/v2/major-production/excel/preview",
        data={"group_code": GROUP, "domain": "PLC"},
        files={"file": (
            "conflict.xlsx",
            _excel_bytes(igr=igr, itr=itr),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )},
    )
    assert preview.status_code == 200, preview.text
    row = preview.json()["rows"][0]
    assert "CASE_IDENTITY_CONFLICT" in row["blocking_reasons"]
    confirm = client.post(
        "/api/v2/major-production/excel/confirm",
        data={"batch_id": preview.json()["batch_id"]},
    )
    assert confirm.status_code == 409
    assert _counts(client) == before
    assert repository.events(case_by_itr["case_id"])[0]["event_id"] == existing_event["event_id"]
    assert repository.case_detail(case_by_igr["case_id"])["events"] == []


def test_repeated_document_and_excel_intake_remain_idempotent(tmp_path: Path) -> None:
    client = _client(tmp_path)
    itr = "ITR-506-IDEMPOTENT"
    igr = "IGR-506-IDEMPOTENT"

    first_document = _intake_document(client, itr=itr, file_name="stable-report.docx")
    second_document = _intake_document(client, itr=itr, file_name="stable-report.docx")
    first_excel = _import_excel(client, igr=igr, itr=itr, file_name="stable-major.xlsx")
    second_excel = _import_excel(client, igr=igr, itr=itr, file_name="stable-major.xlsx")

    assert len(_case_snapshot(client)) == 1
    assert first_document["case"]["case_id"] == second_document["case"]["case_id"]
    assert first_document["event"]["event_id"] == second_document["event"]["event_id"]
    assert first_document["document"]["version_id"] == second_document["document"]["version_id"]
    assert first_excel["case_ids"] == second_excel["case_ids"] == [first_document["case"]["case_id"]]
    assert _counts(client) == {
        "cases": 1,
        "events": 1,
        "source_facts": 1,
        "documents": 1,
        "source_links": 2,
        "identities": 4,
    }
