"""RC5 package composition and formal F02 provider wiring gate."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app
from scripts.major_repeat_test_rc5 import (
    FORMAL_MOCK_FIXTURE,
    FORMAL_MOCK_FIXTURE_ID,
    create_package_app,
    formal_mock_provider,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/golden/hardware_case_scenarios/A9001-LDO 输出振荡.docx"


def _initialize_p0(db_path: Path) -> None:
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db_path)


def _intake(client: TestClient) -> dict:
    response = client.post(
        "/api/v2/major-production/sources",
        data={
            "title": "RC5 F02 package provider wiring",
            "group_code": "RC5-GATE",
            "domain": "PLC",
            "standard_itr": "ITR-RC5-F02-001",
        },
        files={
            "file": (
                SOURCE.name,
                SOURCE.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_core_provider_none_keeps_fail_closed_503(tmp_path: Path) -> None:
    p0_db = tmp_path / "fail-closed.sqlite3"
    _initialize_p0(p0_db)
    client = TestClient(create_p0_app(
        p0_db,
        project_root=ROOT,
        major_case_db_path=tmp_path / "major.sqlite3",
        major_attachment_root=tmp_path / "attachments",
        major_artifact_root=tmp_path / "artifacts",
        # Intentionally omit major_provider: core default remains fail-closed.
    ))

    created = _intake(client)
    response = client.post(
        f"/api/v2/major-production/cases/{created['case']['case_id']}/analysis"
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "MAJOR_ANALYSIS_PROVIDER_NOT_CONFIGURED"
    assert client.get("/api/v2/historical-cases").json()["total"] == 0


def test_package_composition_wires_f02_provider_and_returns_four_pending_candidates(
    tmp_path: Path,
) -> None:
    p0_db = tmp_path / "package.sqlite3"
    _initialize_p0(p0_db)
    client = TestClient(create_package_app(p0_db))

    assert client.get("/p0/major-production").status_code == 200
    assert client.get("/api/v2/historical-cases").json()["total"] == 0
    created = _intake(client)
    case_id = created["case"]["case_id"]
    event_id = created["event"]["event_id"]
    repository = client.app.state.major_case_repository
    assert repository.entries(case_id) == []
    assert client.post(f"/api/v2/major-production/events/{event_id}/publish").status_code == 409

    pending_specs = [
        {"object_id": f"{event_id}:{unit_id}", "unit_id": unit_id}
        for unit_id in ("ISSUE_FACT", "ROOT_CAUSE", "ACTION", "VERIFICATION")
    ]
    provider_responses = formal_mock_provider({}, pending_specs, {})
    assert len(provider_responses) == 4
    assert [item["object_id"] for item in provider_responses] == [
        item["object_id"] for item in pending_specs
    ]
    assert [item["data"]["content"] for item in provider_responses] == list(
        FORMAL_MOCK_FIXTURE.values()
    )
    assert all(set(item) == {"object_id", "data"} for item in provider_responses)
    assert all(set(item["data"]) == {"content"} for item in provider_responses)
    assert not any(
        forbidden in item
        for item in provider_responses
        for forbidden in (
            "case_id",
            "knowledge_object_id",
            "publication",
            "confirmed_revision",
            "decision",
        )
    )
    assert repository.entries(case_id) == []

    analysis = client.post(f"/api/v2/major-production/cases/{case_id}/analysis")
    assert analysis.status_code == 200, analysis.text
    candidates = analysis.json()["candidates"]
    assert len(candidates) == 4
    assert {item["entry_type"] for item in candidates} == set(FORMAL_MOCK_FIXTURE)
    assert {
        item["entry_type"]: item["content"] for item in candidates
    } == FORMAL_MOCK_FIXTURE
    assert all(item["status"] == "PENDING" and item["origin"] == "AI" for item in candidates)
    assert all(item["assertion_kind"] == "AI_INFERENCE" for item in candidates)
    assert client.get("/api/v2/historical-cases").json()["total"] == 0
    assert client.post(f"/api/v2/major-production/events/{event_id}/publish").status_code == 409
    assert FORMAL_MOCK_FIXTURE_ID == "MAJOR_REPEAT_V11_F02"
