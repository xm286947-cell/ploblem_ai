"""PDF/DOCX parsing into stable evidence fragments."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from zipfile import BadZipFile, ZipFile
import hashlib
import re
import xml.etree.ElementTree as ET

from .pdf_markdown import MarkdownPage, normalize_pdf_to_markdown


PARSER_VERSION = "req022-parser-2-markdown"
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(frozen=True)
class ParsedFragment:
    ordinal: int
    section_path: str
    location_type: str
    location_ref: str
    fragment_type: str
    text: str

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass
class ParseResult:
    media_type: str
    fragments: list[ParsedFragment]
    warnings: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION
    derived_format: str = ""
    converter_mode: str = ""
    quality_status: str = ""
    derived_markdown: str = ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _word_text(element: ET.Element) -> str:
    return _clean("".join(node.text or "" for node in element.iter(f"{WORD_NS}t")))


def _paragraph_style(paragraph: ET.Element) -> str:
    props = paragraph.find(f"{WORD_NS}pPr")
    style = props.find(f"{WORD_NS}pStyle") if props is not None else None
    return style.attrib.get(f"{WORD_NS}val", "") if style is not None else ""


def parse_docx(path: str | Path) -> ParseResult:
    source = Path(path)
    try:
        with ZipFile(source) as archive:
            xml = archive.read("word/document.xml")
    except (BadZipFile, KeyError) as exc:
        raise ValueError("DOCX_INVALID_OR_CORRUPT") from exc
    root = ET.fromstring(xml)
    body = root.find(f"{WORD_NS}body")
    fragments: list[ParsedFragment] = []
    headings: list[str] = []
    paragraph_no = 0
    table_no = 0
    for child in list(body) if body is not None else []:
        if child.tag == f"{WORD_NS}p":
            paragraph_no += 1
            text = _word_text(child)
            if not text:
                continue
            style = _paragraph_style(child).lower()
            heading_match = re.search(r"heading\s*([1-9])|标题\s*([1-9])", style)
            if heading_match:
                level = int(heading_match.group(1) or heading_match.group(2))
                headings[:] = headings[: level - 1]
                headings.append(text)
                continue
            fragments.append(ParsedFragment(
                len(fragments) + 1, " / ".join(headings), "PARAGRAPH", f"paragraph:{paragraph_no}", "TEXT", text
            ))
        elif child.tag == f"{WORD_NS}tbl":
            table_no += 1
            rows = []
            for row in child.findall(f"{WORD_NS}tr"):
                cells = [_word_text(cell) for cell in row.findall(f"{WORD_NS}tc")]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                fragments.append(ParsedFragment(
                    len(fragments) + 1, " / ".join(headings), "TABLE", f"table:{table_no}", "TABLE", "\n".join(rows)
                ))
    warnings = [] if fragments else ["DOCX_NO_READABLE_TEXT"]
    return ParseResult("DOCX", fragments, warnings)


def _is_page_marker(line: str) -> bool:
    return bool(re.fullmatch(r"<!--\s*(?:PDF_PAGE|DOCUMENT_PAGE):\s*\d+\s*-->", line.strip()))


def _is_markdown_table_line(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 3 and stripped.count("|") >= 2 and stripped.startswith("|")


def _markdown_page_fragments(
    page: MarkdownPage,
    start_ordinal: int,
    heading_state: list[str] | None = None,
) -> list[ParsedFragment]:
    fragments: list[ParsedFragment] = []
    headings = heading_state if heading_state is not None else []
    paragraph: list[str] = []
    table: list[str] = []
    local_no = 0

    def emit(lines: list[str], fragment_type: str) -> None:
        nonlocal local_no
        if not lines:
            return
        text = "\n".join(lines).strip()
        if not text:
            return
        local_no += 1
        fragments.append(ParsedFragment(
            start_ordinal + len(fragments),
            " / ".join(headings),
            "PAGE_TABLE" if fragment_type == "TABLE" else "PAGE_MARKDOWN",
            f"page:{page.pdf_page}:markdown:{local_no}",
            fragment_type,
            text,
        ))

    def flush_paragraph() -> None:
        nonlocal paragraph
        emit(paragraph, "TEXT")
        paragraph = []

    def flush_table() -> None:
        nonlocal table
        emit(table, "TABLE")
        table = []

    for raw_line in page.markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if _is_page_marker(stripped):
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if heading:
            flush_paragraph()
            flush_table()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            headings[:] = headings[: level - 1]
            headings.append(title)
            continue
        if _is_markdown_table_line(stripped):
            flush_paragraph()
            table.append(stripped)
            continue
        if table:
            flush_table()
        if not stripped:
            flush_paragraph()
            continue
        paragraph.append(stripped)

    flush_table()
    flush_paragraph()
    return fragments


def parse_pdf(path: str | Path) -> ParseResult:
    document = normalize_pdf_to_markdown(path)
    fragments: list[ParsedFragment] = []
    headings: list[str] = []
    for page in document.pages:
        fragments.extend(
            _markdown_page_fragments(page, len(fragments) + 1, headings)
        )

    warnings = list(document.warnings)
    if not fragments:
        warnings.append("PDF_NO_READABLE_MARKDOWN")
    parser_version = (
        f"{PARSER_VERSION}:{document.converter_mode}:{document.quality_status.lower()}"
    )
    return ParseResult(
        "PDF",
        fragments,
        list(dict.fromkeys(warnings)),
        parser_version=parser_version,
        derived_format="MARKDOWN",
        converter_mode=document.converter_mode,
        quality_status=document.quality_status,
        derived_markdown=document.markdown,
    )


def parse_document(path: str | Path) -> ParseResult:
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix == ".docx":
        return parse_docx(path)
    if suffix == ".doc":
        raise ValueError("LEGACY_DOC_UNSUPPORTED_SAVE_AS_DOCX")
    raise ValueError("UNSUPPORTED_DOCUMENT_TYPE")


def find_itr_mentions(fragments: Iterable[ParsedFragment]) -> list[str]:
    values: list[str] = []
    for fragment in fragments:
        for match in re.findall(r"(?i)\bITR[0-9A-Z_-]{4,}\b", fragment.text):
            value = match.upper()
            if value.endswith("CS"):
                value = value[:-2]
            if value not in values:
                values.append(value)
    return values
