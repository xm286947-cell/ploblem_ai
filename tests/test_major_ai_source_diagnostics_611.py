"""AI source provenance and actionable Runtime diagnostics (#611).

No real Provider credential is used. Browser and live Provider have separate gates.
"""
from __future__ import annotations

from pathlib import Path
from io import BytesIO

from pypdf import PdfWriter

from fastapi.testclient import TestClient

from quality_knowledge.major_cases.runtime_provider import MajorD01ProviderBridge
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app
from runtime.contracts import SourceRef

ROOT = Path(__file__).resolve().parents[1]
XLS_FIXTURE = ROOT / "tests/fixtures/major_v5/cases.xls"


def _pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()

API = "/api/v2/major-production"


def _client(tmp_path: Path, provider) -> TestClient:
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


def _excel_case(client: TestClient) -> tuple[str, str]:
    preview = client.post(
        API + "/excel/preview",
        data={"group_code": "MAJOR", "domain": "QUALITY"},
        files=[
            ("file", ("cases.xls", XLS_FIXTURE.read_bytes(), "application/vnd.ms-excel")),
            ("materials", ("ITR20269951-review.pdf", _pdf(), "application/pdf")),
        ],
    )
    assert preview.status_code == 200, preview.text
    confirmed = client.post(API + "/excel/confirm",
                            data={"batch_id": preview.json()["batch_id"]})
    assert confirmed.status_code == 200, confirmed.text
    case_id = confirmed.json()["result"]["case_ids"][0]
    detail = client.get(f"{API}/cases/{case_id}").json()
    return case_id, detail["events"][0]["event_id"]


def test_bridge_does_not_relabel_excel_evidence_as_pdf() -> None:
    def ref(source_id: str, kind: str) -> SourceRef:
        return SourceRef(
            source_id=source_id, source_type=kind,
            revision="1", content_hash=source_id + "-sha",
            fingerprint=source_id + "-sha", uri="major://" + source_id,
        )
    pdf = ref("pdf-source", "MAJOR_SOURCE_DOCUMENT")
    excel = ref("excel-fact", "MAJOR_EXCEL_SOURCE_FACT")
    def responder(payload, _context):
        assert payload["fragments"][0]["source_type"] == "MAJOR_SOURCE_DOCUMENT"
        assert payload["fragments"][1]["source_type"] == "MAJOR_EXCEL_SOURCE_FACT"
        return [
            {"object_id": "E:ISSUE_FACT", "content": "PDF-derived fact",
             "fragment_ids": ["pdf-fragment"]},
            {"object_id": "E:ROOT_CAUSE", "content": "Excel-derived cause",
             "fragment_ids": ["excel-fragment"]},
        ]
    bridge = MajorD01ProviderBridge(responder)
    items = bridge(
        {"source": pdf.model_dump(mode="json"), "fragments": [
            {"fragment_id": "pdf-fragment", "text_content": "PDF", "source": pdf.model_dump(mode="json")},
            {"fragment_id": "excel-fragment", "text_content": "EXCEL", "source": excel.model_dump(mode="json")},
        ]},
        [{"object_id": "E:ISSUE_FACT"}, {"object_id": "E:ROOT_CAUSE"}],
        {"d01": {"partition_key": "E"}},
    )
    assert len(items) == 2
    assert items[0]["evidence"][0]["source"]["source_id"] == "pdf-source"
    assert items[1]["evidence"][0]["source"]["source_id"] == "excel-fact"


def test_excel_plus_review_document_reach_same_d01_provider_with_distinct_sources(tmp_path: Path) -> None:
    captured = []
    def provider(provider_input, specs, _context):
        captured.append(provider_input)
        return [{"object_id": spec["object_id"], "data": {"content": spec["unit_id"] + " sample"}}
                for spec in specs]

    client = _client(tmp_path, provider)
    case_id, event_id = _excel_case(client)
    repository = client.app.state.major_case_repository
    docx = ROOT / "tests/golden/hardware_case_scenarios/A9001-LDO 输出振荡.docx"
    document = repository.ingest_file(case_id, docx)
    from quality_knowledge.major_cases.document_parser import parse_document
    fragments = repository.save_parse_result(
        document["version_id"],
        parse_document(repository.attachment_path(document["version_id"]))
    )
    assert fragments
    repository.add_source_link(case_id, event_id, {
        "record_id": document["version_id"],
        "source_type": "MAJOR_SOURCE_DOCUMENT",
        "source_system": "MAJOR_SOURCE_INTAKE",
        "group_code": "MAJOR",
        "source_hash": document["content_hash"],
        "file_name": docx.name,
        "version_id": document["version_id"],
        "document_id": document["document_id"],
        "fragment_count": len(fragments),
    }, standard_itr="ITR20269951", role="CURRENT_EVENT", status="LINKED")

    response = client.post(f"{API}/cases/{case_id}/analysis",
                           params={"event_id": event_id})
    assert response.status_code == 200, response.text
    assert len(response.json()["candidates"]) == 4
    assert captured
    input_data = captured[-1]
    types = [item["source"]["source_type"] for item in input_data["fragments"]]
    assert "MAJOR_SOURCE_DOCUMENT" in types
    assert "MAJOR_EXCEL_SOURCE_FACT" in types
    # Includes all parsed review fragments, not the former [:20] sample.
    assert sum(t == "MAJOR_SOURCE_DOCUMENT" for t in types) == len(fragments)
    assert "STRUCTURED SOURCE FACT" in "\n".join(
        item["section_path"] for item in input_data["fragments"]
    )


def test_incomplete_runtime_returns_sanitized_task_diagnostics(tmp_path: Path) -> None:
    client = _client(tmp_path, lambda _input, _specs, _ctx: [])
    case_id, event_id = _excel_case(client)
    failed = client.post(f"{API}/cases/{case_id}/analysis",
                         params={"event_id": event_id})
    assert failed.status_code == 400, failed.text
    assert failed.json()["detail"] == "MAJOR_ANALYSIS_INCOMPLETE"
    task_id = failed.headers.get("X-Major-Runtime-Task-ID")
    assert task_id
    diagnostics = client.get(f"{API}/cases/{case_id}/analysis/diagnostics")
    assert diagnostics.status_code == 200, diagnostics.text
    body = diagnostics.json()
    assert body["tasks"] and body["tasks"][0]["task_id"] == task_id
    assert body["tasks"][0]["committed_objects"] == 0
    assert set(body["tasks"][0]["missing_types"]) == {
        "ISSUE_FACT", "ROOT_CAUSE", "ACTION", "VERIFICATION"}
    assert body["diagnostic_log"].endswith("diagnostics/major_analysis.log")
    # No source bytes, prompts, provider output or credentials in diagnostics.
    for forbidden in ("provider_input", "source_text", "raw_json", "api_key"):
        assert forbidden not in diagnostics.text
    assert Path(body["diagnostic_log"]).exists()
    assert "MAJOR_ANALYSIS_INCOMPLETE" in Path(body["diagnostic_log"]).read_text(encoding="utf-8")
    assert client.get(f"{API}/cases/not-a-case/analysis/diagnostics").status_code == 404


def test_windows_ui_has_safe_duplicate_click_guard_and_diagnostics() -> None:
    js = (ROOT / "quality_knowledge/web/static/major_production.js").read_text("utf-8")
    assert "loadAnalysisDiagnostics()" in js
    assert "analysisBusy" in js
    assert "analyzeButton.disabled = analysisSucceeded" in js
    assert "button.textContent = '正在保存人工确认…'" in js
    assert "button.textContent = '正在正式发布…'" in js
    assert "major-ai611-v1" in (
        ROOT / "quality_knowledge/web/templates/major_production.html"
    ).read_text("utf-8")
