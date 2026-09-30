from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient

from quality_knowledge.web.p0_app import create_p0_app


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


def _client(tmp_path: Path) -> TestClient:
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
    assert "HARDWARE CASE · R1 FIELD VALIDATION" in page.text
    assert "Upload → Parse → Inspect" in page.text
    assert "不调用 Agent" in page.text

    asset = client.get("/p0/static/hardware_case_word_import.js")
    assert asset.status_code == 200
    assert "/r1/word-snapshot" in asset.text


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
