from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZipFile

import pytest

from services.hardware_case_word import parse_docx
from services.hardware_source_identity import parse_source_identity


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


def test_document_snapshot_preserves_heading_paragraph_table_image_and_locators(tmp_path):
    path = tmp_path / "A12345-Flash启动异常.docx"
    raw = _docx(path)
    snapshot = parse_docx(path).to_snapshot()

    assert snapshot["snapshot_version"] == "hardware-document-snapshot/v1"
    assert snapshot["source"]["source_id"] == hashlib.sha256(raw).hexdigest()
    assert snapshot["identity"]["business_case_id"] == "A12345"
    assert snapshot["identity"]["raw_title"] == "Flash启动异常"
    assert snapshot["counts"] == {
        "headings": 1,
        "paragraphs": 2,
        "tables": 1,
        "images": 1,
        "blocks": 5,
    }

    heading = snapshot["structure"]["headings"][0]
    assert heading["text"] == "现象"
    assert heading["source_locator"]["paragraph"] == 1

    paragraph = snapshot["structure"]["paragraphs"][0]
    assert paragraph["section_path"] == ["现象"]
    assert paragraph["source_locator"]["section"] == "现象"

    table = snapshot["structure"]["tables"][0]
    assert table["table_rows"] == [["器件", "结果"], ["Flash", "Fail"]]
    assert table["source_locator"]["table"] == 1

    image = snapshot["structure"]["images"][0]
    assert image["source_locator"]["image"] == 1
    assert image["source_locator"]["paragraph"] == 3
    assert image["image_ref"] == "media/image1.png"


def test_filename_identity_is_fail_safe_not_fabricated():
    digest = "a" * 64
    identity = parse_source_identity("现场异常说明.docx", digest)
    assert identity.business_case_id is None
    assert identity.raw_title == "现场异常说明"
    assert identity.identity_status == "NEEDS_REVIEW"
    assert identity.warnings == ("BUSINESS_CASE_ID_NOT_DERIVED",)


@pytest.mark.parametrize(
    "filename,expected_id,expected_title",
    [
        ("A1234-电源问题.docx", "A1234", "电源问题"),
        ("a98765_连接器异常.docx", "A98765", "连接器异常"),
        ("A5555—Flash失效.docx", "A5555", "Flash失效"),
    ],
)
def test_filename_identity_recognizes_supported_business_pattern(
    filename, expected_id, expected_title
):
    identity = parse_source_identity(filename, "b" * 64)
    assert identity.business_case_id == expected_id
    assert identity.raw_title == expected_title
    assert identity.identity_status == "PARSED"
    assert identity.warnings == ()


def test_source_identity_requires_real_sha256():
    with pytest.raises(ValueError, match="SOURCE_ID_SHA256_REQUIRED"):
        parse_source_identity("A1234-x.docx", "not-a-sha")
