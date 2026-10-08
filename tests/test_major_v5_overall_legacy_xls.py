from __future__ import annotations

from pathlib import Path
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]
XLS_FIXTURE = ROOT / "tests/fixtures/major_v5/cases.xls"
def _pdf_bytes() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


PDF_BYTES = _pdf_bytes()


def _client(tmp_path: Path) -> TestClient:
    p0_db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    app = create_p0_app(
        p0_db,
        stage_runner=object(),
        project_root=ROOT,
        major_case_db_path=tmp_path / "major.db",
        major_attachment_root=tmp_path / "major-attachments",
        major_artifact_root=tmp_path / "artifacts",
    )
    return TestClient(app)


def _preview(client: TestClient, materials: list[tuple[str, bytes]]):
    return client.post(
        "/api/v2/major-production/excel/preview",
        data={"group_code": "MAJOR", "domain": "QUALITY"},
        files=[
            ("file", ("cases.xls", XLS_FIXTURE.read_bytes(), "application/vnd.ms-excel")),
            *[
                ("materials", (name, content, "application/pdf"))
                for name, content in materials
            ],
        ],
    )


def test_golden_80_character_boundary_keeps_complete_sentence() -> None:
    from quality_knowledge.major_cases.restore import derive_description_title

    # The frozen XLS contains this real first sentence followed by 512 MiB.
    description = (
        "持续诊断日志写入场景下，Orion Edge Gateway OG-420 固件 2.8.1 "
        "将迁移后的 retention_count=0 解释为无限保留。"
        "512 MiB /var 分区达到至少 95% 使用率，随后配置写入失败。"
    )
    assert description[79:82] == "512"
    title = derive_description_title(description)
    assert title == description[:79]
    assert title.endswith("解释为无限保留。")
    assert not title.endswith("。5")
    assert "512 MiB" in description

    assert derive_description_title("短标题") == "短标题"
    # Without a sentence break, numeric tokens cannot be cut mid-number.
    numeric = "A" * 78 + " 512 MiB"
    assert derive_description_title(numeric).endswith("…")
    assert not derive_description_title(numeric).endswith(" 5…")


def test_true_binary_xls_exact_pdf_match_converges_into_one_case_event(tmp_path: Path):
    assert XLS_FIXTURE.read_bytes()[:8] == bytes.fromhex("D0CF11E0A1B11AE1")
    client = _client(tmp_path)

    response = _preview(client, [("ITR20269951-review.pdf", PDF_BYTES)])
    assert response.status_code == 200, response.text
    preview = response.json()
    row = preview["rows"][0]
    assert preview["parse_summary"]["total_rows"] == 1
    assert row["mapped_fields"]["itr_id"] == "ITR20269951"
    assert row["mapped_fields"]["report_filename"] == "ITR20269951-review.pdf"
    assert row["report_match"]["match_status"] == "MATCHED"
    assert row["event_resolution"] == {
        "status": "MATCHED",
        "standard_itr": "ITR20269951",
        "reason": "SINGLE_EVENT",
    }
    assert row["report_match"]["event_binding_itr"] == "ITR20269951"
    assert row["report_match"]["event_binding_status"] == "BOUND"

    confirmed = client.post(
        "/api/v2/major-production/excel/confirm",
        data={"batch_id": preview["batch_id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["result"]
    assert result["failed"] == 0
    assert result["created_cases"] == 1
    assert result["events"] == 1
    case = client.get(f"/api/v2/major-production/cases/{result['case_ids'][0]}")
    assert case.status_code == 200
    body = case.json()
    assert [event["standard_itr"] for event in body["events"]] == ["ITR20269951"]
    source_types = {item["source_type"] for item in body["source_links"]}
    assert "MAJOR_EXCEL_SOURCE_FACT" in source_types
    assert "MAJOR_SOURCE_DOCUMENT" in source_types


def test_excel_source_fact_provenance_is_persisted_and_separate_from_pdf(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = _preview(client, [("ITR20269951-review.pdf", PDF_BYTES)])
    assert response.status_code == 200, response.text
    batch_id = response.json()["batch_id"]
    imported = client.post("/api/v2/major-production/excel/confirm", data={"batch_id": batch_id})
    assert imported.status_code == 200, imported.text
    case_id = imported.json()["result"]["case_ids"][0]

    response = client.get(f"/api/v2/major-production/cases/{case_id}/provenance")
    assert response.status_code == 200, response.text
    provenance = response.json()
    facts = provenance["structured_source_facts"]
    assert len(facts) == 1
    fact = facts[0]
    assert fact["source_type"] == "EXCEL"
    assert fact["source_fact_revision_id"].startswith("KSF-")
    assert fact["revision_no"] == 1
    assert fact["source_ref"].startswith("cases.xls#")
    assert len(fact["source_hash"]) == 64
    assert fact["source_link_ids"] and fact["linked"] is True
    assert fact["original_description"]

    docs = provenance["document_evidence"]
    assert len(docs) == 1
    assert docs[0]["filename"] == "ITR20269951-review.pdf"
    assert docs[0]["version_id"]
    detail = client.get(f"/api/v2/major-production/cases/{case_id}").json()
    types = [link["source_type"] for link in detail["source_links"]]
    assert "MAJOR_EXCEL_SOURCE_FACT" in types
    assert "MAJOR_SOURCE_DOCUMENT" in types
    assert "DOCUMENT" not in {
        row["source_type"]
        for row in client.app.state.major_case_restore_service.source_fact_history(case_id)
    }


def test_normalized_collision_is_ambiguous_and_upload_names_are_isolated(tmp_path: Path):
    client = _client(tmp_path)
    response = _preview(
        client,
        [("ITR20269951-review.pdf", PDF_BYTES), ("itr20269951-REVIEW.PDF", PDF_BYTES)],
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    row = preview["rows"][0]
    assert row["report_match"]["match_status"] == "AMBIGUOUS"
    assert len(row["report_match"]["candidate_paths"]) == 2
    staged_reports = Path(row["report_match"]["candidate_paths"][0]).parents[1]
    assert (staged_reports / "upload-0001").is_dir()
    assert (staged_reports / "upload-0002").is_dir()
    assert {Path(path).name for path in row["report_match"]["candidate_paths"]} == {
        "ITR20269951-review.pdf",
        "itr20269951-REVIEW.PDF",
    }
    confirmed = client.post(
        "/api/v2/major-production/excel/confirm",
        data={"batch_id": preview["batch_id"]},
    )
    assert confirmed.status_code == 409
    batch = client.get(f"/api/v2/major-production/excel/batches/{preview['batch_id']}")
    assert batch.json()["result"]["errors"][0]["error"] == "AMBIGUOUS_REPORT_MATCH"


def test_cross_extension_and_itr_fallback_are_not_used(tmp_path: Path):
    client = _client(tmp_path)
    # The frozen real XLS names a PDF. A same-stem DOCX is not an eligible match.
    response = _preview(client, [("ITR20269951-review.docx", b"not used as a PDF")])
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["report_match"]["match_status"] == "NOT_FOUND"
    assert row["report_match"]["matched_report_path"] == ""
    assert row["report_match"]["candidate_paths"] == []


def test_legacy_doc_is_rejected_before_staging(tmp_path: Path):
    client = _client(tmp_path)
    response = _preview(client, [("ITR20269951-review.doc", b"legacy binary Word")])
    assert response.status_code == 400
    assert response.json()["detail"] == "MAJOR_REVIEW_MATERIAL_TYPE_UNSUPPORTED"


def test_safe_normalized_filename_match_and_generic_itr_fallback_forbidden(tmp_path: Path):
    client = _client(tmp_path)
    normalized = _preview(client, [("itr20269951-REVIEW.PDF", PDF_BYTES)])
    assert normalized.status_code == 200, normalized.text
    assert normalized.json()["rows"][0]["report_match"]["match_status"] == "MATCHED"

    other_itr = _preview(client, [("ITR20269951-other.pdf", PDF_BYTES)])
    assert other_itr.status_code == 200, other_itr.text
    row = other_itr.json()["rows"][0]
    assert row["report_match"]["match_status"] == "NOT_FOUND"
    assert row["report_match"]["candidate_paths"] == []
