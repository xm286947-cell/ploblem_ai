from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

import quality_knowledge.major_cases.document_parser as document_parser
import quality_knowledge.major_cases.pdf_markdown as pdf_markdown
from quality_knowledge.major_cases.document_parser import (
    ParseResult,
    ParsedFragment,
    parse_docx,
    parse_pdf,
)
from quality_knowledge.major_cases.pdf_markdown import MarkdownDocument, MarkdownPage
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository


def _page(page: int, body: str, raw_text: str = "") -> MarkdownPage:
    return MarkdownPage(
        pdf_page=page,
        document_page=page,
        markdown=(
            f"<!-- PDF_PAGE: {page} -->\n"
            f"<!-- DOCUMENT_PAGE: {page} -->\n\n"
            f"{body}"
        ),
        raw_text=raw_text or body,
    )


def test_pdf_markdown_projection_preserves_page_section_and_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "review.pdf"
    source.write_bytes(b"%PDF-synthetic")
    derived = MarkdownDocument(
        source_path=str(source),
        converter_mode="pymupdf4llm",
        pages=[
            _page(
                1,
                "## TRC发生\n\n边界保护不足导致状态竞争。\n\n"
                "### 纠正措施\n\n"
                "| 措施 | Owner | 状态 |\n"
                "| --- | --- | --- |\n"
                "| 原子提交 | Team-A | Done |",
            ),
            _page(2, "纠正措施在第二页继续描述，仍应保持上一页章节上下文。"),
            _page(3, "## MRC流出\n\n评审检查项未覆盖该并发窗口。"),
        ],
        markdown="synthetic",
        quality_status="PASS",
    )
    monkeypatch.setattr(
        document_parser,
        "normalize_pdf_to_markdown",
        lambda _path: derived,
    )

    result = parse_pdf(source)

    assert result.media_type == "PDF"
    assert result.derived_format == "MARKDOWN"
    assert result.converter_mode == "pymupdf4llm"
    assert result.quality_status == "PASS"
    assert result.parser_version.endswith(":pymupdf4llm:pass")

    trc = next(item for item in result.fragments if "边界保护不足" in item.text)
    assert trc.section_path == "TRC发生"
    assert trc.location_ref.startswith("page:1:markdown:")
    assert trc.location_type == "PAGE_MARKDOWN"

    table = next(item for item in result.fragments if item.fragment_type == "TABLE")
    assert table.section_path == "TRC发生 / 纠正措施"
    assert "| 措施 | Owner | 状态 |" in table.text
    assert table.location_ref.startswith("page:1:markdown:")

    continued = next(item for item in result.fragments if "第二页继续" in item.text)
    assert continued.section_path == "TRC发生 / 纠正措施"
    assert continued.location_ref.startswith("page:2:markdown:")

    mrc = next(item for item in result.fragments if "评审检查项" in item.text)
    assert mrc.section_path == "MRC流出"
    assert mrc.location_ref.startswith("page:3:markdown:")


def test_pdf_markdown_fallback_is_explicit_not_silent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "fallback.pdf"
    source.write_bytes(b"%PDF-synthetic")

    def fail_primary(_path: Path):
        raise RuntimeError("primary unavailable")

    monkeypatch.setattr(pdf_markdown, "_convert_with_pymupdf4llm", fail_primary)
    monkeypatch.setattr(
        pdf_markdown,
        "_convert_with_pymupdf",
        lambda _path: [_page(1, "## Root Cause\n\nFallback content " + ("x" * 100))],
    )

    result = pdf_markdown.normalize_pdf_to_markdown(source)

    assert result.source_authority == "PDF"
    assert result.derived_format == "MARKDOWN"
    assert result.converter_mode == "pymupdf"
    assert any(
        warning.startswith("MARKDOWN_PRIMARY_FALLBACK:")
        for warning in result.warnings
    )
    assert "<!-- PDF_PAGE: 1 -->" in result.markdown


def test_markdown_quality_detects_flattened_parameter_table() -> None:
    raw = (
        "Parameter Symbol Typ Max Unit\n"
        + "\n".join(str(index) for index in range(20))
        + "\nNote 1 applies"
    )
    page = _page(1, raw, raw_text=raw)

    status, warnings = pdf_markdown._quality_check([page])

    assert status == "WARN"
    assert "MARKDOWN_TABLE_STRUCTURE_SUSPECTED" in warnings
    assert "MARKDOWN_NUMERIC_FLATTENING_SUSPECTED" in warnings


def test_docx_parsing_contract_remains_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "review.docx"
    xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>TRC发生</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>原有 DOCX 段落解析保持不变</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>措施</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>状态</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>修复</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>完成</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)

    result = parse_docx(path)

    assert result.media_type == "DOCX"
    assert result.derived_format == ""
    assert result.converter_mode == ""
    assert result.fragments[0].section_path == "TRC发生"
    assert result.fragments[0].text == "原有 DOCX 段落解析保持不变"
    assert result.fragments[1].fragment_type == "TABLE"
    assert "措施 | 状态" in result.fragments[1].text


def test_repository_keeps_pdf_as_authority_and_records_converter_identity(
    tmp_path: Path,
) -> None:
    repository = MajorKnowledgeRepository(
        tmp_path / "major.db",
        tmp_path / "attachments",
    )
    case = repository.create_case("Markdown authority boundary", "MAJOR", "QUALITY")
    source = tmp_path / "review.pdf"
    source.write_bytes(b"%PDF-source-authority")
    version = repository.ingest_file(case["case_id"], source)

    result = ParseResult(
        "PDF",
        [
            ParsedFragment(
                1,
                "TRC发生",
                "PAGE_MARKDOWN",
                "page:1:markdown:1",
                "TEXT",
                "PDF-derived semantic evidence",
            )
        ],
        [],
        parser_version="req022-parser-2-markdown:pymupdf4llm:pass",
        derived_format="MARKDOWN",
        converter_mode="pymupdf4llm",
        quality_status="PASS",
        derived_markdown="<!-- PDF_PAGE: 1 -->\n\nPDF-derived semantic evidence",
    )
    repository.save_parse_result(version["version_id"], result)

    stored = repository.version(version["version_id"])
    assert stored is not None
    assert stored["media_type"] == "PDF"
    assert stored["attachment_path"].endswith(".pdf")
    assert stored["parser_version"] == "req022-parser-2-markdown:pymupdf4llm:pass"
    assert repository.fragments(version["version_id"])[0]["location_ref"] == "page:1:markdown:1"
    assert not list((tmp_path / "attachments").rglob("*.md"))


def test_pymupdf4llm_primary_converter_smoke(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("pymupdf4llm")

    source = tmp_path / "primary.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "TRC occurrence review evidence " + ("A" * 120))
    document.save(str(source))
    document.close()

    result = pdf_markdown.normalize_pdf_to_markdown(source)

    assert result.converter_mode == "pymupdf4llm"
    assert result.source_authority == "PDF"
    assert result.derived_format == "MARKDOWN"
    assert "<!-- PDF_PAGE: 1 -->" in result.markdown
    assert "TRC occurrence review evidence" in result.markdown
