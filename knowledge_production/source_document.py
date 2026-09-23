from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from parser.pdf_extractor import PdfExtractor, PdfExtractionResult
from repositories import JsonArtifactRepository

from .models import RetrievalStatus, SourceDocument, StructuredDocument, StructuredTextBlock


class SourceDocumentError(RuntimeError):
    pass


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_segment(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    value = value.strip(".-")
    if not value:
        raise SourceDocumentError("SOURCE_ID_INVALID")
    return value


def _detect_section(text: str, previous: str | None) -> str | None:
    for raw in text.splitlines()[:25]:
        line = raw.strip()
        if not line or len(line) > 120:
            continue
        if re.match(r"^(?:\d+(?:\.\d+)*)\s+[A-Z0-9][^.!?]{1,100}$", line, re.I):
            return line
        letters = [ch for ch in line if ch.isalpha()]
        if len(letters) >= 4 and line.upper() == line:
            return line
    return previous


def _table_text_by_page(extraction: PdfExtractionResult) -> dict[int, str]:
    """Project parsed PDF tables back into page-scoped source text.

    PdfExtractor already obtains table cells through pdfplumber.  The
    StructuredDocument fact layer must not drop that source evidence merely
    because pypdf's free-text extraction omits or mangles table field IDs.
    """
    grouped: dict[int, list[str]] = {}
    for table in extraction.tables:
        if not isinstance(table, dict):
            continue
        page_ref = table.get("page_ref")
        rows = table.get("rows")
        if not isinstance(page_ref, int) or page_ref < 1:
            continue
        if not isinstance(rows, list):
            continue

        lines: list[str] = []
        for row in rows:
            if not isinstance(row, list):
                continue
            cells = [str(cell or "").strip() for cell in row]
            if any(cells):
                lines.append("\t".join(cells))
        if not lines:
            continue

        table_index = table.get("table_index")
        label = (
            f"[TABLE {table_index}]"
            if isinstance(table_index, int)
            else "[TABLE]"
        )
        grouped.setdefault(page_ref, []).append(
            label + "\n" + "\n".join(lines)
        )

    return {
        page: "\n\n".join(chunks)
        for page, chunks in grouped.items()
    }


class SourceDocumentService:
    """Immutable official-source ingestion and page-traceable parsing."""

    def __init__(
        self,
        repository: JsonArtifactRepository,
        *,
        extractor: PdfExtractor | None = None,
    ) -> None:
        self.repository = repository
        self.extractor = extractor or PdfExtractor()

    def ingest_pdf(
        self,
        pdf_path: str | Path,
        *,
        source_id: str,
        publisher: str,
        title: str,
        version: str | None = None,
        revision: str | None = None,
        official_url: str | None = None,
        source_ref: str | None = None,
        language: str = "en",
    ) -> tuple[SourceDocument, StructuredDocument]:
        path = Path(pdf_path)
        if not path.is_file():
            raise SourceDocumentError("SOURCE_UNAVAILABLE")
        if path.suffix.lower() != ".pdf":
            raise SourceDocumentError("SOURCE_DOCUMENT_NOT_PDF")

        payload = path.read_bytes()
        content_hash = _sha256_bytes(payload)
        source_key = _safe_segment(source_id)
        declared_version = (revision or version or "").strip()
        source_version = _safe_segment(declared_version) if declared_version else content_hash
        base = f"knowledge/source_documents/{source_key}/{source_version}"
        manifest_path = f"{base}/source_document.json"
        original_path = f"{base}/original.pdf"
        structured_path = f"{base}/structured_document.json"

        existing = self.repository.load(manifest_path)
        if existing is not None:
            existing_hash = str(existing.get("content_hash") or "")
            if existing_hash != content_hash:
                raise SourceDocumentError("SOURCE_VERSION_CONFLICT")
            document = SourceDocument.model_validate(existing)
            structured_payload = self.repository.load(structured_path, required=True)
            assert structured_payload is not None
            return document, StructuredDocument.model_validate(structured_payload)

        target_pdf = self.repository.resolve(original_path)
        target_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target_pdf)

        document = SourceDocument(
            source_id=source_id,
            source_version=source_version,
            publisher=publisher,
            title=title,
            version=version,
            revision=revision,
            official_url=official_url,
            source_ref=source_ref,
            local_cache_ref=self.repository.relative(target_pdf),
            original_file_name=path.name,
            content_hash=content_hash,
            language=language,
            retrieval_status=RetrievalStatus.RECEIVED,
        )
        self.repository.save(manifest_path, document.model_dump(mode="json"))

        try:
            extraction = self.extractor.extract(target_pdf)
            structured = self._build_structured_document(document, extraction)
        except Exception as exc:
            failed = document.model_copy(update={"retrieval_status": RetrievalStatus.FAILED})
            self.repository.save(manifest_path, failed.model_dump(mode="json"))
            raise SourceDocumentError("PDF_PARSE_FAILED") from exc

        parsed = document.model_copy(update={"retrieval_status": RetrievalStatus.PARSED})
        self.repository.save(manifest_path, parsed.model_dump(mode="json"))
        self.repository.save(structured_path, structured.model_dump(mode="json"))
        return parsed, structured

    @staticmethod
    def _build_structured_document(
        document: SourceDocument,
        extraction: PdfExtractionResult,
    ) -> StructuredDocument:
        blocks: list[StructuredTextBlock] = []
        current_section: str | None = None
        table_text = _table_text_by_page(extraction)

        for page in extraction.pages:
            current_section = _detect_section(page.text, current_section)
            page_table_text = table_text.get(page.page_number, "")
            source_text = page.text
            if page_table_text:
                source_text = "\n\n".join(
                    value
                    for value in (page.text, page_table_text)
                    if value
                )

            blocks.append(
                StructuredTextBlock(
                    source_id=document.source_id,
                    source_version=document.source_version,
                    page=page.page_number,
                    section=current_section,
                    source_text=source_text,
                    source_anchor=f"page:{page.page_number}",
                    content_hash=_sha256_text(source_text),
                )
            )
        return StructuredDocument(
            source_id=document.source_id,
            source_version=document.source_version,
            parse_status="PARSED",
            page_count=extraction.page_count,
            blocks=blocks,
            warnings=list(extraction.warnings),
        )
