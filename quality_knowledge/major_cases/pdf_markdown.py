"""PDF -> Markdown normalization for the Major knowledge intake path.

The original PDF remains the source authority. Markdown is a derived,
AI-readable representation that preserves page markers and as much document
structure as the selected converter can provide.

This module intentionally contains no Major business semantics. It is a thin
normalization boundary feeding the existing ParsedFragment / EvidenceFusion
pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Iterable
import re

from parser.pdf_extractor import PdfExtractor


MARKDOWN_CONTRACT_VERSION = "major-pdf-markdown/v1"
PDF_PAGE_MARKER = "<!-- PDF_PAGE: {page} -->"
DOCUMENT_PAGE_MARKER = "<!-- DOCUMENT_PAGE: {page} -->"


@dataclass(frozen=True)
class MarkdownPage:
    pdf_page: int
    document_page: int
    markdown: str
    raw_text: str = ""


@dataclass
class MarkdownDocument:
    source_path: str
    converter_mode: str
    pages: list[MarkdownPage]
    markdown: str
    quality_status: str
    warnings: list[str] = field(default_factory=list)
    source_authority: str = "PDF"
    derived_format: str = "MARKDOWN"
    contract_version: str = MARKDOWN_CONTRACT_VERSION


def _normalize_newlines(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _wrap_page(markdown: str, page_number: int) -> str:
    body = _normalize_newlines(markdown)
    markers = (
        PDF_PAGE_MARKER.format(page=page_number),
        DOCUMENT_PAGE_MARKER.format(page=page_number),
    )
    return "\n".join(markers) + ("\n\n" + body if body else "")


def _open_pymupdf(path: Path):
    try:
        import pymupdf  # type: ignore
        return pymupdf.open(str(path))
    except ImportError:
        import fitz  # type: ignore
        return fitz.open(str(path))


def _pymupdf_text_as_markdown(page) -> tuple[str, str]:
    """Best-effort structural fallback when pymupdf4llm is unavailable.

    Heading inference is deliberately conservative; if layout evidence is not
    strong, text stays plain rather than inventing section structure.
    """
    raw_text = _normalize_newlines(page.get_text("text") or "")
    try:
        data = page.get_text("dict") or {}
    except Exception:
        return raw_text, raw_text

    spans: list[dict] = []
    for block in data.get("blocks") or []:
        if int(block.get("type", 0) or 0) != 0:
            continue
        for line in block.get("lines") or []:
            spans.extend(span for span in line.get("spans") or [] if str(span.get("text") or "").strip())
    sizes = [float(span.get("size") or 0) for span in spans if float(span.get("size") or 0) > 0]
    body_size = median(sizes) if sizes else 0.0

    output: list[str] = []
    for block in data.get("blocks") or []:
        if int(block.get("type", 0) or 0) != 0:
            continue
        block_lines: list[str] = []
        for line in block.get("lines") or []:
            line_spans = [span for span in line.get("spans") or [] if str(span.get("text") or "").strip()]
            text = "".join(str(span.get("text") or "") for span in line_spans).strip()
            if not text:
                continue
            max_size = max((float(span.get("size") or 0) for span in line_spans), default=0.0)
            is_heading = (
                body_size > 0
                and max_size >= max(body_size + 1.5, body_size * 1.18)
                and len(text) <= 140
            )
            block_lines.append(("## " if is_heading else "") + text)
        if block_lines:
            output.extend(block_lines)
            output.append("")
    markdown = _normalize_newlines("\n".join(output)) or raw_text
    return markdown, raw_text


def _convert_with_pymupdf4llm(path: Path) -> list[MarkdownPage]:
    import pymupdf4llm  # type: ignore

    document = _open_pymupdf(path)
    try:
        pages: list[MarkdownPage] = []
        for index in range(len(document)):
            page = document[index]
            raw_text = _normalize_newlines(page.get_text("text") or "")
            markdown = pymupdf4llm.to_markdown(document, pages=[index])
            if isinstance(markdown, list):
                markdown = "\n\n".join(
                    str(item.get("text") if isinstance(item, dict) else item or "")
                    for item in markdown
                )
            elif isinstance(markdown, dict):
                markdown = str(markdown.get("text") or "")
            if not str(markdown or "").strip() and raw_text:
                markdown, _ = _pymupdf_text_as_markdown(page)
            pages.append(
                MarkdownPage(
                    pdf_page=index + 1,
                    document_page=index + 1,
                    markdown=_wrap_page(str(markdown or ""), index + 1),
                    raw_text=raw_text,
                )
            )
        return pages
    finally:
        document.close()


def _convert_with_pymupdf(path: Path) -> list[MarkdownPage]:
    document = _open_pymupdf(path)
    try:
        pages: list[MarkdownPage] = []
        for index in range(len(document)):
            markdown, raw_text = _pymupdf_text_as_markdown(document[index])
            pages.append(
                MarkdownPage(
                    pdf_page=index + 1,
                    document_page=index + 1,
                    markdown=_wrap_page(markdown, index + 1),
                    raw_text=raw_text,
                )
            )
        return pages
    finally:
        document.close()


def _legacy_markdown(path: Path) -> list[MarkdownPage]:
    """Last-resort compatibility projection using the pre-existing extractor."""
    result = PdfExtractor().extract(path)
    tables_by_page: dict[int, list[str]] = {}
    for table in result.tables:
        page = int(table.get("page_ref") or 0)
        rows = table.get("rows") or []
        if not page or not rows:
            continue
        widths = max((len(row) for row in rows), default=0)
        normalized = [list(row) + [""] * (widths - len(row)) for row in rows]
        if widths:
            table_lines = [
                "| " + " | ".join(str(cell or "") for cell in normalized[0]) + " |",
                "| " + " | ".join("---" for _ in range(widths)) + " |",
            ]
            table_lines.extend(
                "| " + " | ".join(str(cell or "") for cell in row) + " |"
                for row in normalized[1:]
            )
            tables_by_page.setdefault(page, []).append("\n".join(table_lines))

    pages: list[MarkdownPage] = []
    for page in result.pages:
        parts = [page.text] if page.text else []
        parts.extend(tables_by_page.get(page.page_number, []))
        body = "\n\n".join(part for part in parts if str(part).strip())
        pages.append(
            MarkdownPage(
                pdf_page=page.page_number,
                document_page=page.page_number,
                markdown=_wrap_page(body, page.page_number),
                raw_text=page.text,
            )
        )
    return pages


def _table_column_warnings(markdown: str) -> list[str]:
    warnings: list[str] = []
    run: list[str] = []

    def inspect(lines: Iterable[str]) -> None:
        widths = []
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            widths.append(max(stripped.count("|") - 1, 0))
        widths = [value for value in widths if value]
        if len(widths) >= 2 and len(set(widths)) > 1:
            warnings.append("MARKDOWN_TABLE_COLUMN_MISMATCH")

    for line in markdown.splitlines():
        if line.strip().startswith("|") and line.strip().endswith("|"):
            run.append(line)
        else:
            if run:
                inspect(run)
                run = []
    if run:
        inspect(run)
    return warnings


def _quality_check(pages: list[MarkdownPage]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not pages:
        return "FAIL", ["MARKDOWN_NO_PAGES"]

    combined = "\n\n".join(page.markdown for page in pages)
    raw_combined = "\n\n".join(page.raw_text for page in pages)

    marker_count = len(re.findall(r"<!--\s*PDF_PAGE:\s*\d+\s*-->", combined))
    if marker_count != len(pages):
        warnings.append("MARKDOWN_PAGE_MARKER_INCOMPLETE")

    visible = re.sub(r"<!--.*?-->", "", combined, flags=re.DOTALL)
    visible = re.sub(r"[#|*_>\-]", " ", visible)
    if len(re.sub(r"\s+", "", visible)) < 80:
        warnings.append("LOW_TEXT_DOCUMENT")

    numeric_lines = [
        line for line in combined.splitlines()
        if re.fullmatch(r"\s*[-+]?\d+(?:\.\d+)?(?:\s*[A-Za-z%°]+)?\s*", line)
    ]
    nonempty_lines = [line for line in combined.splitlines() if line.strip()]
    if len(numeric_lines) >= 12 and len(numeric_lines) / max(len(nonempty_lines), 1) >= 0.25:
        warnings.append("MARKDOWN_NUMERIC_FLATTENING_SUSPECTED")

    corpus = raw_combined or combined
    english_table_signature = all(
        re.search(rf"(?i)\b{term}\b", corpus)
        for term in ("parameter", "typ", "max", "unit")
    )
    chinese_table_signature = all(term in corpus for term in ("参数", "典型", "最大", "单位"))
    table_signature = english_table_signature or chinese_table_signature
    has_markdown_table = bool(re.search(r"(?m)^\s*\|.+\|\s*$", combined))
    if table_signature and not has_markdown_table:
        warnings.append("MARKDOWN_TABLE_STRUCTURE_SUSPECTED")

    warnings.extend(_table_column_warnings(combined))

    raw_note = bool(re.search(r"(?im)^\s*(?:note|footnote)\b", raw_combined))
    md_note = bool(re.search(r"(?im)^\s*(?:note|footnote)\b", combined))
    if raw_note and not md_note:
        warnings.append("MARKDOWN_NOTE_FOOTNOTE_LOSS_SUSPECTED")

    warnings = list(dict.fromkeys(warnings))
    fatal = {"MARKDOWN_NO_PAGES", "MARKDOWN_PAGE_MARKER_INCOMPLETE"}
    if fatal.intersection(warnings):
        return "FAIL", warnings
    return ("WARN" if warnings else "PASS"), warnings


def normalize_pdf_to_markdown(path: str | Path) -> MarkdownDocument:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"PDF文件不存在: {source}")

    warnings: list[str] = []
    converter_mode = "pymupdf4llm"
    try:
        pages = _convert_with_pymupdf4llm(source)
    except Exception as primary_error:
        warnings.append(f"MARKDOWN_PRIMARY_FALLBACK:{type(primary_error).__name__}")
        converter_mode = "pymupdf"
        try:
            pages = _convert_with_pymupdf(source)
        except Exception as fallback_error:
            warnings.append(f"MARKDOWN_PYMUPDF_FALLBACK:{type(fallback_error).__name__}")
            converter_mode = "existing_fallback"
            pages = _legacy_markdown(source)

    quality_status, quality_warnings = _quality_check(pages)
    warnings.extend(quality_warnings)
    markdown = "\n\n".join(page.markdown for page in pages)
    return MarkdownDocument(
        source_path=str(source),
        converter_mode=converter_mode,
        pages=pages,
        markdown=markdown,
        quality_status=quality_status,
        warnings=list(dict.fromkeys(warnings)),
    )
