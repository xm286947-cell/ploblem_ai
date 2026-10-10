from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient

from quality_knowledge.web.p0_app import create_p0_app
from services.hardware_asset_repository import CandidateAssetRepository


MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _docx(path: Path) -> bytes:
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}">
<w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>现象</w:t></w:r></w:p>
<w:p><w:r><w:t>设备启动异常</w:t></w:r></w:p>
<w:tbl>
<w:tr><w:tc><w:p><w:r><w:t>器件</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>结果</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>Flash</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Fail</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
<w:p><w:r><w:t>波形如下</w:t></w:r><w:r><w:drawing><a:blip r:embed="rId5"/></w:drawing></w:r></w:p>
</w:body></w:document>"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId5" Target="media/image1.png" Type="image"/>
</Relationships>"""
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("word/media/image1.png", b"not-real-image")
    return path.read_bytes()


def _bootstrap_asset_schema(tmp_path: Path) -> None:
    # The normal HardwareStartupCoordinator FIRST_INSTALL performs this step.
    # Standalone create_p0_app tests must explicitly initialize the same schema,
    # including hardware_asset_operation_journal, before exercising Word APIs.
    CandidateAssetRepository(tmp_path / "hardware_asset.db").initialize()


def _client(tmp_path: Path) -> TestClient:
    _bootstrap_asset_schema(tmp_path)
    return TestClient(
        create_p0_app(
            tmp_path / "quality.db",
            hardware_case_db_path=tmp_path / "hardware.db",
            hardware_tree_upload_dir=tmp_path / "tree_uploads",
            hardware_case_source_root=tmp_path / "sources",
            enabled_domains={"HARDWARE_CASE"},
        )
    )


def test_word_import_page_is_in_existing_hardware_case_shell(tmp_path: Path):
    client = _client(tmp_path)
    home = client.get("/p0/hardware-cases")
    assert home.status_code == 200
    assert 'href="/p0/hardware-cases/word-import"' in home.text
    assert "案例 Word 导入" in home.text

    page = client.get("/p0/hardware-cases/word-import")
    assert page.status_code == 200
    assert "HARDWARE CASE · R1 GOLDEN KNOWLEDGE" in page.text
    assert "Upload → Parse → Inspect" in page.text
    assert "Markdown Agent View" in page.text
    assert "Run / Resume Agent" in page.text
    assert "Retry Failed Stage" in page.text
    assert "Force Full Run" in page.text
    assert "Stage Execution Summary" in page.text
    assert "Golden Knowledge Preview" in page.text

    asset = client.get("/p0/static/hardware_case_word_import.js")
    assert asset.status_code == 200
    assert "/r1/word-snapshot" in asset.text
    assert "/r1/agent-extract" in asset.text
    assert "force_full_run=true" in asset.text
    assert "retry_failed_stage=" in asset.text
    assert "Transport Retry" in asset.text
    assert "Validation Retry" in asset.text
    assert "Recovery Calls" in asset.text
    assert "Input Size" in asset.text
    assert "Attempt" in asset.text
    assert "data-stage-a-summary" in page.text
    assert "data-stage-b-summary" in page.text
    assert "/r1/previews?limit=10" in asset.text
    assert "autoRestore:true" in asset.text
    assert "data-delete-preview-id" in asset.text
    assert "method:'DELETE'" in asset.text
    assert "仅删除本地 Golden Preview 测试记录，不删除原始 Word、Source Fact 或正式知识。" in asset.text
    assert "data-clear-previews" in page.text
    assert "清空历史" in page.text


def test_docx_upload_renders_frozen_snapshot_contract(tmp_path: Path):
    client = _client(tmp_path)
    source = tmp_path / "A12345-Flash启动异常.docx"
    raw = _docx(source)

    blocked = client.post(
        "/api/v2/hardware-cases/r1/word-snapshot",
        files={"file": (source.name, raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert blocked.status_code == 403

    response = client.post(
        "/api/v2/hardware-cases/r1/word-snapshot",
        files={"file": (source.name, raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers=MAINTAINER,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot_version"] == "hardware-document-snapshot/v1"
    assert payload["source"]["source_id"] == hashlib.sha256(raw).hexdigest()
    assert payload["identity"]["business_case_id"] == "A12345"
    assert payload["identity"]["raw_title"] == "Flash启动异常"
    assert payload["identity"]["identity_status"] == "PARSED"
    assert payload["structure"]["headings"][0]["text"] == "现象"
    assert payload["structure"]["paragraphs"][0]["source_locator"]["section"] == "现象"
    assert payload["structure"]["tables"][0]["table_rows"] == [["器件", "结果"], ["Flash", "Fail"]]
    assert payload["structure"]["images"][0]["image_ref"] == "media/image1.png"
    assert payload["structure"]["images"][0]["source_locator"]["image"] == 1
    assert payload["markdown_view"]["view_version"] == "hardware-markdown-view/v1"
    assert "HC_BLOCK" in payload["markdown_view"]["markdown"]
    assert "| 器件 | 结果 |" in payload["markdown_view"]["markdown"]


def test_abnormal_filename_is_fail_safe_and_non_docx_is_rejected(tmp_path: Path):
    client = _client(tmp_path)
    source = tmp_path / "现场异常说明.docx"
    raw = _docx(source)
    response = client.post(
        "/api/v2/hardware-cases/r1/word-snapshot",
        files={"file": (source.name, raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers=MAINTAINER,
    )
    assert response.status_code == 200
    identity = response.json()["identity"]
    assert identity["business_case_id"] is None
    assert identity["raw_title"] == "现场异常说明"
    assert identity["identity_status"] == "NEEDS_REVIEW"
    assert identity["warnings"] == ["BUSINESS_CASE_ID_NOT_DERIVED"]

    rejected = client.post(
        "/api/v2/hardware-cases/r1/word-snapshot",
        files={"file": ("case.pdf", b"pdf", "application/pdf")},
        headers=MAINTAINER,
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "DOCX_REQUIRED"


def test_r1_agent_poc_uses_injected_unified_runtime_and_evidence_gate(tmp_path: Path):
    captured = {}

    def structurer(document):
        captured.update(document)
        paragraph_ids = [
            item["block_id"]
            for item in document["evidence_index"]
            if item.get("block_type") == "PARAGRAPH"
        ]
        evidence_id = paragraph_ids[0]
        return {
            "title": "Flash启动异常",
            "product_context": {},
            "facts": {
                "symptom": {
                    "value": "设备启动异常",
                    "evidence_block_ids": [evidence_id],
                }
            },
            "circuit_feature_links": [],
            "material_links": [],
        }

    _bootstrap_asset_schema(tmp_path)
    client = TestClient(
        create_p0_app(
            tmp_path / "quality.db",
            hardware_case_db_path=tmp_path / "hardware.db",
            hardware_tree_upload_dir=tmp_path / "tree_uploads",
            hardware_case_source_root=tmp_path / "sources",
            hardware_case_r1_structurer=structurer,
            enabled_domains={"HARDWARE_CASE"},
        )
    )
    source = tmp_path / "A12345-Flash启动异常.docx"
    raw = _docx(source)
    parsed = client.post(
        "/api/v2/hardware-cases/r1/word-snapshot",
        files={"file": (source.name, raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers=MAINTAINER,
    )
    assert parsed.status_code == 200

    blocked = client.post(
        "/api/v2/hardware-cases/r1/agent-extract",
        json=parsed.json(),
    )
    assert blocked.status_code == 403

    response = client.post(
        "/api/v2/hardware-cases/r1/agent-extract",
        json=parsed.json(),
        headers=MAINTAINER,
    )
    assert response.status_code == 200
    payload = response.json()
    assert captured["input_contract"] == "hardware-case-r1-agent-input/v3"
    assert "markdown_view" not in captured
    assert "markdown_text" not in captured
    assert "blocks" not in captured
    assert "valid_block_ids" not in captured
    assert "HC_BLOCK" in captured["markdown"]
    assert "tree_candidates" not in captured
    assert captured["evidence_index"]
    assert "evidence_blocks" not in captured
    assert all("text" not in item for item in captured["evidence_index"])
    assert payload["status"] == "PASS"
    assert payload["evidence_validation"]["status"] == "PASS"
    assert payload["evidence_validation"]["fabricated_fact_count"] == 0
    assert payload["knowledge_object"]["contract_version"] == "hardware-case-knowledge-object/v1"
    assert payload["knowledge_object"]["review"]["object_status"] == "CANDIDATE"
    assert payload["preview"]["preview_id"] == 1

    history = client.get("/api/v2/hardware-cases/r1/previews?limit=10", headers=MAINTAINER)
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["source_id"] == parsed.json()["source"]["source_id"]

    latest = client.get("/api/v2/hardware-cases/r1/previews/latest", headers=MAINTAINER)
    assert latest.status_code == 200
    assert latest.json()["result"]["knowledge_object"]["review"]["object_status"] == "CANDIDATE"
    assert latest.json()["snapshot"]["source"]["source_id"] == parsed.json()["source"]["source_id"]

    reopened = client.get("/api/v2/hardware-cases/r1/previews/1", headers=MAINTAINER)
    assert reopened.status_code == 200
    assert reopened.json()["preview_id"] == 1

def test_r1_preview_cleanup_api_is_local_only_and_empty_clear_is_idempotent(tmp_path: Path):
    client = _client(tmp_path)
    store = client.app.state.hardware_r1_preview_store

    source_root = tmp_path / "sources"
    source_root.mkdir(parents=True, exist_ok=True)
    preserved_word = source_root / "A0152-preserved.docx"
    preserved_word.write_bytes(b"word-source-sentinel")

    hardware_db = tmp_path / "hardware.db"
    hardware_before = hardware_db.read_bytes()

    preview_snapshot = {
        "snapshot_version": "hardware-document-snapshot/v1",
        "source": {"source_id": "a" * 64, "file_name": "A0152-preserved.docx"},
        "identity": {
            "source_id": "a" * 64,
            "business_case_id": "A0152",
            "raw_title": "CPU_串口输出配置",
            "identity_status": "PARSED",
            "warnings": [],
        },
        "structure": {
            "blocks": [
                {"block_id": "B1", "block_type": "PARAGRAPH", "text": "MCU"}
            ]
        },
    }
    saved1 = store.save(preview_snapshot, {"status": "PASS", "runtime": {"run_id": "run-1"}})
    saved2 = store.save(preview_snapshot, {"status": "PASS", "runtime": {"run_id": "run-2"}})

    blocked = client.delete(
        f"/api/v2/hardware-cases/r1/previews/{saved1['preview_id']}"
    )
    assert blocked.status_code == 403

    deleted = client.delete(
        f"/api/v2/hardware-cases/r1/previews/{saved1['preview_id']}",
        headers=MAINTAINER,
    )
    assert deleted.status_code == 200
    assert deleted.json()["scope"] == "LOCAL_GOLDEN_PREVIEW_ONLY"
    assert store.by_id(saved1["preview_id"]) is None
    assert store.by_id(saved2["preview_id"]) is not None

    cleared = client.delete(
        "/api/v2/hardware-cases/r1/previews",
        headers=MAINTAINER,
    )
    assert cleared.status_code == 200
    assert cleared.json()["deleted_count"] == 1
    assert store.list(limit=10) == []

    empty = client.delete(
        "/api/v2/hardware-cases/r1/previews",
        headers=MAINTAINER,
    )
    assert empty.status_code == 200
    assert empty.json()["deleted_count"] == 0

    assert preserved_word.read_bytes() == b"word-source-sentinel"
    assert hardware_db.read_bytes() == hardware_before
    assert preview_snapshot["source"]["source_id"] == "a" * 64
    assert preview_snapshot["identity"]["raw_title"] == "CPU_串口输出配置"

def test_v14_agent_execution_mode_query_contract_is_fail_closed(tmp_path: Path):
    client = _client(tmp_path)

    conflict = client.post(
        "/api/v2/hardware-cases/r1/agent-extract?force_full_run=true&retry_failed_stage=STAGE_B",
        headers=MAINTAINER,
        json={},
    )
    assert conflict.status_code == 400
    assert conflict.json()["detail"] == "EXECUTION_MODE_CONFLICT"

    invalid = client.post(
        "/api/v2/hardware-cases/r1/agent-extract?retry_failed_stage=UNKNOWN",
        headers=MAINTAINER,
        json={},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "RETRY_FAILED_STAGE_INVALID"

