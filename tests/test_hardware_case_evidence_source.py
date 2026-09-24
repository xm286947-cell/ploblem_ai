from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.hardware_case_api import create_hardware_case_router
from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_source_store import (
    HardwareCaseSourceError,
    HardwareCaseSourceStore,
)


MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}

DOC_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>原因分析</w:t></w:r></w:p>
  <w:p><w:r><w:t>输入浪涌触发保护。</w:t></w:r></w:p>
  <w:tbl>
    <w:tr><w:tc><w:p><w:r><w:t>测试项</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>结果</w:t></w:r></w:p></w:tc></w:tr>
  </w:tbl>
 </w:body>
</w:document>
"""


def _docx(path: Path, name: str = "A9001-source.docx") -> Path:
    target = path / name
    with ZipFile(target, "w") as archive:
        archive.writestr("word/document.xml", DOC_XML)
    return target


def _case(status: str = "PUBLISHED") -> dict:
    return {
        "case_id": "HC-SOURCE-001",
        "title": "Synthetic source case",
        "case_status": status,
        "processing_status": "READY",
        "source_refs": ["word:A9001-source.docx"],
        "product_context": {"product": "Synthetic"},
        "facts": {},
    }


def _stack(tmp_path: Path, *, status: str = "PUBLISHED"):
    db = tmp_path / "hardware_case.sqlite3"
    backend = HardwareCaseBackendService(HardwareCaseRepository(db))
    source_store = HardwareCaseSourceStore(db, tmp_path / "sources")
    backend.create_case(_case(status))
    backend.save_evidence(
        {
            "evidence_id": "EV-SOURCE-1",
            "case_id": "HC-SOURCE-001",
            "source_ref": "word:A9001-source.docx",
            "evidence_type": "TEXT",
            "locator": {"block_id": "B0002", "paragraph": 2, "section": "原因分析"},
            "excerpt_or_caption": "输入浪涌触发保护。",
            "evidence_status": "AVAILABLE",
        }
    )
    app = FastAPI()
    app.include_router(
        create_hardware_case_router(
            backend,
            source_store=source_store,
        )
    )
    return TestClient(app), backend, source_store


def test_source_store_register_preview_and_never_exposes_absolute_path(tmp_path: Path):
    source = _docx(tmp_path)
    store = HardwareCaseSourceStore(tmp_path / "db.sqlite3", tmp_path / "sources")

    meta = store.register_file("word:A9001-source.docx", source)
    serialized = repr(meta)
    assert str(tmp_path) not in serialized
    assert "relative_path" not in meta
    assert meta["source_status"] == "AVAILABLE"

    preview = store.preview(
        "word:A9001-source.docx",
        {"block_id": "B0002"},
        context_blocks=1,
    )
    assert preview["preview_status"] == "AVAILABLE"
    matched = [item for item in preview["blocks"] if item["matched"]]
    assert matched[0]["text"] == "输入浪涌触发保护。"
    assert str(tmp_path) not in repr(preview)


def test_source_store_fails_closed_when_file_missing_or_hash_changes(tmp_path: Path):
    source = _docx(tmp_path)
    store = HardwareCaseSourceStore(tmp_path / "db.sqlite3", tmp_path / "sources")
    store.register_file("word:A9001-source.docx", source)

    stored = store.resolve_path("word:A9001-source.docx")
    stored.unlink()
    try:
        store.resolve_path("word:A9001-source.docx")
        assert False, "expected SOURCE_UNAVAILABLE"
    except HardwareCaseSourceError as error:
        assert error.code == "SOURCE_UNAVAILABLE"

    store.register_file("word:A9001-source.docx", source)
    stored = store.resolve_path("word:A9001-source.docx")
    stored.write_bytes(b"tampered")
    try:
        store.resolve_path("word:A9001-source.docx")
        assert False, "expected HASH_MISMATCH"
    except HardwareCaseSourceError as error:
        assert error.code == "HASH_MISMATCH"


def test_evidence_scoped_source_api_enforces_case_visibility_and_returns_preview(tmp_path: Path):
    client, _, _ = _stack(tmp_path, status="PENDING_REVIEW")
    source = _docx(tmp_path)
    with source.open("rb") as stream:
        upload = client.post(
            "/api/v2/hardware-cases/sources/upload",
            data={"source_ref": "word:A9001-source.docx"},
            files={
                "file": (
                    "..\\unsafe\\A9001-source.docx",
                    stream,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=MAINTAINER,
        )
    assert upload.status_code == 201
    assert upload.json()["display_name"] == "A9001-source.docx"
    assert str(tmp_path) not in repr(upload.json())

    blocked = client.get(
        "/api/v2/hardware-cases/HC-SOURCE-001/evidence/EV-SOURCE-1/source-meta"
    )
    assert blocked.status_code == 404

    allowed = client.get(
        "/api/v2/hardware-cases/HC-SOURCE-001/evidence/EV-SOURCE-1/source-meta",
        headers=MAINTAINER,
    )
    assert allowed.status_code == 200
    assert allowed.json()["source"]["source_status"] == "AVAILABLE"
    assert str(tmp_path) not in repr(allowed.json())

    preview = client.get(
        "/api/v2/hardware-cases/HC-SOURCE-001/evidence/EV-SOURCE-1/source-preview",
        headers=MAINTAINER,
    )
    assert preview.status_code == 200
    assert any(
        item["matched"] and item["text"] == "输入浪涌触发保护。"
        for item in preview.json()["blocks"]
    )

    wrong_evidence = client.get(
        "/api/v2/hardware-cases/HC-SOURCE-001/evidence/EV-NOT-THERE/source-meta",
        headers=MAINTAINER,
    )
    assert wrong_evidence.status_code == 404
    assert wrong_evidence.json()["detail"] == "EVIDENCE_NOT_FOUND"


def test_published_consumer_can_download_only_evidence_owned_source(tmp_path: Path):
    client, _, _ = _stack(tmp_path, status="PUBLISHED")
    source = _docx(tmp_path)
    with source.open("rb") as stream:
        assert client.post(
            "/api/v2/hardware-cases/sources/upload",
            data={"source_ref": "word:A9001-source.docx"},
            files={"file": (source.name, stream)},
            headers=MAINTAINER,
        ).status_code == 201

    meta = client.get(
        "/api/v2/hardware-cases/HC-SOURCE-001/evidence/EV-SOURCE-1/source-meta"
    )
    assert meta.status_code == 200

    response = client.get(
        "/api/v2/hardware-cases/HC-SOURCE-001/evidence/EV-SOURCE-1/source-file"
    )
    assert response.status_code == 200
    assert response.content == source.read_bytes()
    assert "A9001-source.docx" in response.headers.get("content-disposition", "")
