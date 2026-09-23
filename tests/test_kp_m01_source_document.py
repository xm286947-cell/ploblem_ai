from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_production import SourceDocumentError, SourceDocumentService
from parser.pdf_extractor import PageText, PdfExtractionResult
from repositories import JsonArtifactRepository


class FakeExtractor:
    def extract(self, pdf_path: str | Path) -> PdfExtractionResult:
        return PdfExtractionResult(
            report_file=str(pdf_path),
            page_count=2,
            pages=[
                PageText(1, "1 DEVICE HEALTH\nLife time estimation overview", 46, []),
                PageText(2, "PRE_EOL_INFO indicates pre end-of-life status", 45, []),
            ],
            tables=[],
            total_characters=91,
            warnings=[],
        )


def _write_pdf(path: Path, content: bytes = b"%PDF-kp-m01") -> None:
    path.write_bytes(content)


def test_source_document_and_parse_are_traceable(tmp_path: Path) -> None:
    pdf = tmp_path / "emmc-health.pdf"
    _write_pdf(pdf)
    service = SourceDocumentService(JsonArtifactRepository(tmp_path / "repo"), extractor=FakeExtractor())

    document, structured = service.ingest_pdf(
        pdf,
        source_id="JEDEC-EMMC-HEALTH",
        publisher="JEDEC",
        title="eMMC Device Health",
        revision="R1",
        official_url="https://example.invalid/emmc.pdf",
    )

    assert document.retrieval_status == "PARSED"
    assert document.content_hash
    assert document.source_version == "R1"
    assert document.local_cache_ref.endswith("/R1/original.pdf")
    assert structured.page_count == 2
    assert [b.page for b in structured.blocks] == [1, 2]
    assert structured.blocks[0].section == "1 DEVICE HEALTH"
    assert structured.blocks[0].source_text.startswith("1 DEVICE HEALTH")
    assert structured.blocks[0].source_anchor == "page:1"
    assert structured.blocks[0].content_hash


def test_same_revision_same_content_is_idempotent(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _write_pdf(pdf)
    repo = JsonArtifactRepository(tmp_path / "repo")
    service = SourceDocumentService(repo, extractor=FakeExtractor())

    first = service.ingest_pdf(pdf, source_id="SRC", publisher="P", title="T", revision="R1")
    second = service.ingest_pdf(pdf, source_id="SRC", publisher="P", title="T", revision="R1")

    assert first[0].content_hash == second[0].content_hash
    assert len(repo.list("knowledge/source_documents/SRC/R1")) == 2


def test_same_revision_different_content_is_blocked(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _write_pdf(pdf, b"%PDF-one")
    service = SourceDocumentService(JsonArtifactRepository(tmp_path / "repo"), extractor=FakeExtractor())
    service.ingest_pdf(pdf, source_id="SRC", publisher="P", title="T", revision="R1")

    _write_pdf(pdf, b"%PDF-two")
    with pytest.raises(SourceDocumentError, match="SOURCE_VERSION_CONFLICT"):
        service.ingest_pdf(pdf, source_id="SRC", publisher="P", title="T", revision="R1")


def test_different_revisions_coexist(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _write_pdf(pdf, b"%PDF-one")
    repo = JsonArtifactRepository(tmp_path / "repo")
    service = SourceDocumentService(repo, extractor=FakeExtractor())
    first, _ = service.ingest_pdf(pdf, source_id="SRC", publisher="P", title="T", revision="R1")

    _write_pdf(pdf, b"%PDF-two")
    second, _ = service.ingest_pdf(pdf, source_id="SRC", publisher="P", title="T", revision="R2")

    assert first.source_version == "R1"
    assert second.source_version == "R2"
    assert repo.exists("knowledge/source_documents/SRC/R1/original.pdf")
    assert repo.exists("knowledge/source_documents/SRC/R2/original.pdf")


def test_hash_version_used_when_revision_missing(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _write_pdf(pdf, b"%PDF-no-revision")
    service = SourceDocumentService(JsonArtifactRepository(tmp_path / "repo"), extractor=FakeExtractor())

    document, _ = service.ingest_pdf(pdf, source_id="SRC", publisher="P", title="T")

    assert document.source_version == document.content_hash
