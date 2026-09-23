from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List
import re

from pypdf import PdfReader


@dataclass
class PageText:
    page_number: int
    text: str
    character_count: int
    warnings: List[str] = field(default_factory=list)


@dataclass
class PdfExtractionResult:
    report_file: str
    page_count: int
    pages: List[PageText]
    tables: List[dict]
    total_characters: int
    warnings: List[str]


class PdfExtractor:
    def __init__(self, minimum_page_characters: int = 20, minimum_document_characters: int = 80) -> None:
        self.minimum_page_characters = minimum_page_characters
        self.minimum_document_characters = minimum_document_characters

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[\t\u00a0]+", " ", text)
        text = re.sub(r"[ ]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def extract(self, pdf_path: str | Path) -> PdfExtractionResult:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF文件不存在: {path}")

        reader = PdfReader(str(path))
        pages: List[PageText] = []
        warnings: List[str] = []
        tables: List[dict] = []
        low_text_pages = 0

        for index, page in enumerate(reader.pages, start=1):
            page_warnings: List[str] = []
            try:
                raw_text = page.extract_text() or ""
            except Exception as exc:  # pypdf may fail on malformed page content
                raw_text = ""
                page_warnings.append(f"PAGE_TEXT_EXTRACT_FAILED:{type(exc).__name__}")

            text = self._normalize_text(raw_text)
            if len(text) < self.minimum_page_characters:
                low_text_pages += 1
                page_warnings.append("LOW_TEXT_PAGE")
            pages.append(PageText(index, text, len(text), page_warnings))

        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                for page_index, pdf_page in enumerate(pdf.pages, start=1):
                    for table_index, table in enumerate(pdf_page.extract_tables() or [], start=1):
                        normalized_rows = [
                            [self._normalize_text(str(cell or "")) for cell in row]
                            for row in table
                        ]
                        if any(any(cell for cell in row) for row in normalized_rows):
                            tables.append({
                                "page_ref": page_index,
                                "table_index": table_index,
                                "rows": normalized_rows,
                            })
        except ImportError:
            warnings.append("PDF_TABLE_EXTRACTOR_UNAVAILABLE")
        except Exception as exc:
            warnings.append(f"PDF_TABLE_EXTRACT_FAILED:{type(exc).__name__}")

        total_characters = sum(page.character_count for page in pages)
        if total_characters < self.minimum_document_characters:
            warnings.append("LOW_TEXT_DOCUMENT")
        if pages and low_text_pages / len(pages) >= 0.70:
            warnings.append("SCANNED_PDF_SUSPECTED")

        return PdfExtractionResult(
            report_file=str(path),
            page_count=len(pages),
            pages=pages,
            tables=tables,
            total_characters=total_characters,
            warnings=warnings,
        )
