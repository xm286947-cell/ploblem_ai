"""PDF/DOCX parsing into stable evidence fragments."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from zipfile import BadZipFile, ZipFile
import hashlib
import re
import xml.etree.ElementTree as ET

from parser.pdf_extractor import PdfExtractor

PARSER_VERSION = "req022-parser-1"
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


def parse_pdf(path: str | Path) -> ParseResult:
    result = PdfExtractor().extract(path)
    fragments: list[ParsedFragment] = []
    for page in result.pages:
        if page.text:
            fragments.append(ParsedFragment(
                len(fragments) + 1, "", "PAGE", f"page:{page.page_number}", "TEXT", page.text
            ))
    for table in result.tables:
        text = "\n".join(" | ".join(row) for row in table["rows"])
        fragments.append(ParsedFragment(
            len(fragments) + 1, "", "PAGE_TABLE",
            f"page:{table['page_ref']}:table:{table['table_index']}", "TABLE", text,
        ))
    return ParseResult("PDF", fragments, list(result.warnings))


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
