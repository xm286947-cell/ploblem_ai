"""P0 regression: Windows refresh must re-read durable import/review state.

This test intentionally does not claim a real browser click or Provider E2E.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from io import BytesIO

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]
XLS_FIXTURE = ROOT / "tests/fixtures/major_v5/cases.xls"


def _client(tmp_path: Path, provider=None) -> TestClient:
    p0_db = tmp_path / "p0.sqlite3"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    return TestClient(create_p0_app(
        p0_db, stage_runner=object(), project_root=ROOT,
        major_case_db_path=tmp_path / "major.sqlite3",
        major_attachment_root=tmp_path / "attachments",
        major_artifact_root=tmp_path / "artifacts",
        major_provider=provider,
    ))


def _pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def _preview(client: TestClient) -> dict:
    response = client.post(
        "/api/v2/major-production/excel/preview",
        data={"group_code": "MAJOR", "domain": "QUALITY"},
        files=[
            ("file", ("cases.xls", XLS_FIXTURE.read_bytes(), "application/vnd.ms-excel")),
            ("materials", ("ITR20269951-review.pdf", _pdf(), "application/pdf")),
        ],
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_refresh_preview_uses_existing_batch_and_preserves_snapshot(tmp_path: Path) -> None:
    client = _client(tmp_path)
    preview = _preview(client)
    batch_id = preview["batch_id"]
    before = client.app.state.major_case_restore_service.batch(batch_id)
    response = client.get(f"/api/v2/major-production/excel/batches/{batch_id}")
    assert response.status_code == 200, response.text
    loaded = response.json()
    assert loaded["status"] == "PREVIEW"
    assert loaded["preview"]["rows"] == preview["rows"]
    assert loaded["preview"]["mapping_version"] == preview["mapping_version"]
    assert loaded["current_mapping_version"] == preview["mapping_version"]
    assert loaded["governance"]["preview_sha256"] == before["governance"]["preview_sha256"]
    assert client.app.state.major_case_restore_service.batch(batch_id)["status"] == "PREVIEW"
    recent = client.get("/api/v2/major-production/recent")
    assert recent.status_code == 200, recent.text
    assert recent.json()["batches"][0]["batch_id"] == batch_id
    assert "preview_json" not in recent.json()["batches"][0]
    assert "staging_path" not in recent.json()["batches"][0]

    result = client.post("/api/v2/major-production/excel/confirm", data={"batch_id": batch_id})
    assert result.status_code == 200, result.text
    again = client.get(f"/api/v2/major-production/excel/batches/{batch_id}").json()
    assert again["status"] == "COMPLETED"
    assert again["result"]["case_ids"] == result.json()["result"]["case_ids"]
    detail = client.get(f"/api/v2/major-production/cases/{again['result']['case_ids'][0]}")
    assert detail.status_code == 200
    assert detail.json()["source_links"]
    # Reload must not replay commit or create a duplicate case.
    assert len(client.app.state.major_case_repository.list_cases()["items"]) == 1
    recent_after = client.get("/api/v2/major-production/recent").json()
    assert recent_after["cases"][0]["case_id"] == again["result"]["case_ids"][0]


def test_refresh_case_recovers_pending_and_human_confirmed_revisions(tmp_path: Path) -> None:
    def provider(_input, specs, _ctx):
        return [{"object_id": spec["object_id"], "data": {"content": spec["unit_id"] + " example"}}
                for spec in specs]

    client = _client(tmp_path, provider=provider)
    source = ROOT / "tests/golden/hardware_case_scenarios/A9001-LDO 输出振荡.docx"
    created = client.post(
        "/api/v2/major-production/sources",
        data={"title": "refresh", "group_code": "RECOVERY-605", "domain": "PLC",
              "standard_itr": "ITR-RECOVERY-605"},
        files={"file": (source.name, source.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["case"]["case_id"]
    event_id = created.json()["event"]["event_id"]
    analyzed = client.post(f"/api/v2/major-production/cases/{case_id}/analysis",
                           params={"event_id": event_id})
    assert analyzed.status_code == 200, analyzed.text
    entries = analyzed.json()["candidates"]
    assert len(entries) == 4
    one = entries[0]
    confirmed = client.post(f"/api/v2/major-production/entries/{one['entry_id']}/confirm",
                            json={"reviewer": "tester", "content": "human revised", "reason": "review"})
    assert confirmed.status_code == 200, confirmed.text
    detail = client.get(f"/api/v2/major-production/cases/{case_id}")
    assert detail.status_code == 200, detail.text
    saved = detail.json()
    assert saved["events"][0]["event_id"] == event_id
    assert len(saved["entries"]) == 4
    assert sum(item["status"] == "PENDING" for item in saved["entries"]) == 3
    human = next(item for item in saved["entries"] if item["entry_id"] == one["entry_id"])
    assert human["status"] == "CONFIRMED"
    assert human["content"] == "human revised"
    # Read-only recovery cannot re-invoke AI Provider or publish anything.
    assert client.get("/api/v2/historical-cases").json()["total"] == 0


def test_windows_refresh_ui_wires_restoration_not_reexecution() -> None:
    js = (ROOT / "quality_knowledge/web/static/major_production.js").read_text(encoding="utf-8")
    assert "window.localStorage.setItem(CONTEXT_KEY" in js
    assert "window.sessionStorage.setItem(DRAFT_KEY" in js
    assert "async function restoreCase(caseId, requestedEventId)" in js
    assert "async function restoreBatch(batchId)" in js
    assert "resumeOnLoad();" in js
    assert "api + '/excel/batches/'" in js
    assert "api + '/cases/'" in js
    assert "api + '/recent'" in js
    assert "data-major-recent" in js
    assert "data-major-recovered-confirm" in js
    assert "EVENT_SELECTION_REQUIRED" in js
    assert "window.addEventListener('beforeunload'" in js
    assert "localStorage.setItem(CONTEXT_KEY, JSON.stringify({" in js
    assert "source_bytes" not in js
