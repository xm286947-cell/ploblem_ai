"""GP01 browser production flow from a clean state; provider is the sole mock boundary."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app
from retriever.case_retriever import QueryInput


ROOT = Path(__file__).resolve().parents[1]


def _provider(_provider_input, pending_specs, _context):
    """The test provider boundary returns candidates, never repository state."""
    contents = {
        "ISSUE_FACT": "控制器在掉电恢复后启动失败",
        "ROOT_CAUSE": "掉电窗口内配置写入未完成",
        "ACTION": "增加原子写入和启动恢复校验",
        "VERIFICATION": "完成 100 次掉电恢复验证",
    }
    return [
        {"object_id": item["object_id"], "data": {"content": contents[item["unit_id"]]}}
        for item in pending_specs
    ]


def _client(tmp_path: Path) -> TestClient:
    p0_db = tmp_path / "p0.sqlite3"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    return TestClient(create_p0_app(
        p0_db,
        stage_runner=object(),
        major_case_db_path=tmp_path / "major.sqlite3",
        major_attachment_root=tmp_path / "attachments",
        major_artifact_root=tmp_path / "published",
        major_provider=_provider,
    ))


def test_gp01_clean_state_browser_source_to_publish_and_repeat_contract(tmp_path: Path) -> None:
    client = _client(tmp_path)

    # GP01-01/02: the same browser host starts empty and has a formal entry.
    assert client.get("/p0/major-production").status_code == 200
    assert client.get("/api/v2/historical-cases").json()["total"] == 0

    # GP01-03/04: a real supported document is uploaded through the user API.
    source = ROOT / "tests/golden/hardware_case_scenarios/A9001-LDO 输出振荡.docx"
    intake = client.post(
        "/api/v2/major-production/sources",
        data={
            "title": "掉电恢复启动失败",
            "group_code": "GP01",
            "domain": "PLC",
            "standard_itr": "ITR-GP01-001",
        },
        files={"file": (source.name, source.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert intake.status_code == 201, intake.text
    created = intake.json()
    case_id = created["case"]["case_id"]
    event_id = created["event"]["event_id"]
    assert created["document"]["version_id"]
    assert created["source_link"]["standard_itr"] == "ITR-GP01-001"

    # GP01-05/06: no AI candidate can be published before real analysis/review.
    assert client.post(f"/api/v2/major-production/events/{event_id}/publish").status_code == 409
    analysis = client.post(f"/api/v2/major-production/cases/{case_id}/analysis")
    assert analysis.status_code == 200, analysis.text
    candidates = analysis.json()["candidates"]
    assert {item["entry_type"] for item in candidates} == {"ISSUE_FACT", "ROOT_CAUSE", "ACTION", "VERIFICATION"}
    assert all(item["status"] == "PENDING" and item["origin"] == "AI" for item in candidates)
    assert client.post(f"/api/v2/major-production/events/{event_id}/publish").status_code == 409

    # GP01-07/08: every confirmation is a human revision, not an AI publish.
    for candidate in candidates:
        confirmed = client.post(
            f"/api/v2/major-production/entries/{candidate['entry_id']}/confirm",
            json={"reviewer": "gp01-reviewer", "content": candidate["content"], "reason": "Evidence reviewed"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "CONFIRMED"
        assert confirmed.json()["revision_no"] == 2

    # GP01-09/10: existing publisher creates one stable Historical Case.
    published = client.post(f"/api/v2/major-production/events/{event_id}/publish")
    assert published.status_code == 200, published.text
    publication = published.json()
    assert publication["status"] == "CREATED"
    assert publication["publication_status"] == "PUBLISHED"
    stable_case_id = publication["case_id"]
    assert stable_case_id.startswith("HCASE-")

    # GP01-11/12/13: Case Library and detail read only the published contract.
    library = client.get("/api/v2/historical-cases?q=ITR-GP01-001")
    assert library.status_code == 200
    assert [item["case_id"] for item in library.json()["items"]] == [stable_case_id]
    detail = client.get(f"/api/v2/historical-cases/{stable_case_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["case_id"] == stable_case_id
    assert body["contract_version"] == "historical-case/v1"
    assert body["case_version"] == publication["knowledge_revision"]
    assert body["evidence"] and body["evidence"][0]["raw_text"]
    assert client.get(f"/p0/cases/{stable_case_id}").status_code == 200

    # GP01-14/15: retrieval and Repeat consume the artifact contract, not Major SQLite.
    repeat_cases = client.app.state.repeat_risk_service.cases.search_repeat_cases(
        QueryInput(text="掉电恢复启动失败"), top_k=5
    )
    assert repeat_cases["contract_version"] == "historical-case/v1"
    assert repeat_cases["candidates"][0]["case_id"] == stable_case_id

    # A later analysis archives only pending AI output: confirmed review remains intact.
    assert client.post(f"/api/v2/major-production/cases/{case_id}/analysis").status_code == 200
    major_detail = client.get(f"/api/v2/major-production/cases/{case_id}").json()
    confirmed = [item for item in major_detail["entries"] if item["status"] == "CONFIRMED"]
    assert len(confirmed) == 4
    assert all(item["revision_no"] == 2 for item in confirmed)
    assert all(item["event_id"] == event_id for item in confirmed)
