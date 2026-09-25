from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from knowledge_production import (
    KnowledgeExtractionService,
    KnowledgeReleaseService,
    SourceDocument,
    SourceDocumentService,
    StructuredDocument,
    create_processing_app,
)
from repositories import JsonArtifactRepository


class StorageKnowledgeProductError(RuntimeError):
    pass


def project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "knowledge_production").is_dir() and (parent / "repositories").exists():
            return parent
    raise StorageKnowledgeProductError("KNOWLEDGE_PRODUCT_RUNTIME_NOT_PACKAGED")


def repository_root() -> Path:
    configured = os.environ.get("STORAGE_KNOWLEDGE_REPOSITORY_DIR", "").strip()
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        root = project_root() / "data" / "knowledge_repository"
    root.mkdir(parents=True, exist_ok=True)
    return root


def repository() -> JsonArtifactRepository:
    return JsonArtifactRepository(repository_root())


def processing_app():
    return create_processing_app(repository_root())


def ingest_source(
    payload: bytes,
    *,
    filename: str,
    source_id: str,
    publisher: str,
    title: str,
    version: str = "",
    revision: str = "",
    official_url: str = "",
) -> dict:
    if not filename.lower().endswith(".pdf"):
        raise StorageKnowledgeProductError("SOURCE_DOCUMENT_NOT_PDF")
    if not source_id.strip() or not publisher.strip() or not title.strip():
        raise StorageKnowledgeProductError("SOURCE_METADATA_REQUIRED")
    upload_root = repository_root() / "_uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    temp = upload_root / f"{uuid4().hex}-{Path(filename).name}"
    temp.write_bytes(payload)
    try:
        document, structured = SourceDocumentService(repository()).ingest_pdf(
            temp,
            source_id=source_id.strip(),
            publisher=publisher.strip(),
            title=title.strip(),
            version=version.strip() or None,
            revision=revision.strip() or None,
            official_url=official_url.strip() or None,
        )
    finally:
        temp.unlink(missing_ok=True)
    return {
        "source_document": document.model_dump(mode="json"),
        "structured_document": {
            "source_id": structured.source_id,
            "source_version": structured.source_version,
            "parse_status": structured.parse_status,
            "page_count": structured.page_count,
            "block_count": len(structured.blocks),
            "warnings": structured.warnings,
        },
    }


def _source_pair(source_id: str, source_version: str) -> tuple[SourceDocument, StructuredDocument]:
    repo = repository()
    base = f"knowledge/source_documents/{source_id}/{source_version}"
    source = repo.load(f"{base}/source_document.json")
    structured = repo.load(f"{base}/structured_document.json")
    if not isinstance(source, dict) or not isinstance(structured, dict):
        raise StorageKnowledgeProductError("SOURCE_UNAVAILABLE")
    return SourceDocument.model_validate(source), StructuredDocument.model_validate(structured)


def extract_source(
    source_id: str,
    source_version: str,
    *,
    requested_topics: list[str] | None = None,
) -> dict:
    root = project_root()
    model_config = Path(
        os.environ.get(
            "STORAGE_MODEL_CONFIG",
            str(root / "config" / "model.windows.real.yaml"),
        )
    )
    if not model_config.is_absolute():
        model_config = (root / model_config).resolve()
    bootstrap = KnowledgeExtractionService.from_project(
        root,
        model_config_path=model_config,
        runtime_db_path=repository_root() / "runtime" / "knowledge_production_runtime.sqlite3",
        environ=dict(os.environ),
    )
    service = KnowledgeExtractionService(repository(), bootstrap.runtime)
    source, structured = _source_pair(source_id, source_version)
    candidates = service.extract(
        source,
        structured,
        requested_topics=requested_topics or [],
    )
    return {
        "source_id": source_id,
        "source_version": source_version,
        "candidate_count": len(candidates),
        "candidate_ids": [item.candidate_id for item in candidates],
        "status": "PENDING_REVIEW",
        "review_url": "/knowledge-production/candidates",
    }


def build_and_activate_release(release_version: str) -> dict:
    version = release_version.strip()
    if not version:
        raise StorageKnowledgeProductError("KNOWLEDGE_RELEASE_VERSION_REQUIRED")
    repo = repository()
    manifest = KnowledgeReleaseService(repo).build(
        version,
        created_at=datetime.now(timezone.utc),
    )
    source = repo.resolve(f"knowledge/production/releases/{version}")
    root = project_root() / "knowledge_release"
    root.mkdir(parents=True, exist_ok=True)
    current = root / "current"
    staged = root / f".next-{uuid4().hex}"
    backup = root / f".previous-{uuid4().hex}"
    shutil.copytree(source, staged)
    try:
        if current.exists():
            current.rename(backup)
        staged.rename(current)
    except Exception:
        if current.exists() and not backup.exists():
            shutil.rmtree(current, ignore_errors=True)
        if backup.exists() and not current.exists():
            backup.rename(current)
        raise
    finally:
        shutil.rmtree(staged, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
    return {
        "status": "READY",
        "knowledge_release_version": manifest.knowledge_release_version,
        "snapshot_hash": manifest.snapshot_hash,
        "object_count": manifest.object_count,
        "evidence_count": manifest.evidence_count,
        "source_reference_count": manifest.source_reference_count,
    }
