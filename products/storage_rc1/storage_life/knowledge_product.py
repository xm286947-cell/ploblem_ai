from __future__ import annotations

import os
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
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
from knowledge_production.release_binding import validate_release_binding


class StorageKnowledgeProductError(RuntimeError):
    pass


def project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "knowledge_production").is_dir() and (parent / "repositories").exists():
            return parent
    raise StorageKnowledgeProductError("KNOWLEDGE_PRODUCT_RUNTIME_NOT_PACKAGED")


def model_config_path() -> Path:
    """Use the same effective model config contract as Storage Runtime.

    STORAGE_MODEL_CONFIG is the only override.  Otherwise the Storage package's
    config/model.local.yaml is authoritative for both parameter extraction and
    Knowledge Production.
    """
    product_root = Path(__file__).resolve().parents[1]
    configured = os.environ.get("STORAGE_MODEL_CONFIG", "").strip()
    path = Path(configured).expanduser() if configured else (product_root / "config" / "model.local.yaml")
    if not path.is_absolute():
        path = (product_root / path).resolve()
    else:
        path = path.resolve()
    if not path.is_file():
        raise StorageKnowledgeProductError(
            f"STORAGE_MODEL_CONFIG_MISSING:{path}"
        )
    return path


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
    candidate_metadata: dict | None = None,
    candidate_enricher: Callable[[Any], dict[str, Any]] | None = None,
) -> dict:
    root = project_root()
    model_config = model_config_path()
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
        candidate_metadata=candidate_metadata or {},
        candidate_enricher=candidate_enricher,
    )
    return {
        "source_id": source_id,
        "source_version": source_version,
        "candidate_count": len(candidates),
        "candidate_ids": [item.candidate_id for item in candidates],
        "status": "PENDING_REVIEW",
        "review_url": "/knowledge-production/candidates",
    }


def knowledge_release_root() -> Path:
    configured = os.environ.get("STORAGE_KNOWLEDGE_RELEASE_DIR", "").strip()
    if configured:
        selected = Path(configured).expanduser().resolve()
        return selected.parent if selected.name == "current" else selected.parent
    return (Path(__file__).resolve().parents[1] / "knowledge_release").resolve()


def _default_binding_template() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[3]
        / "contracts"
        / "release_binding"
        / "v1"
        / "release_binding.json"
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageKnowledgeProductError(
            "KNOWLEDGE_RELEASE_BINDING_TEMPLATE_INVALID"
        ) from exc
    if not isinstance(payload, dict):
        raise StorageKnowledgeProductError(
            "KNOWLEDGE_RELEASE_BINDING_TEMPLATE_INVALID"
        )
    return payload


def build_release_candidate(release_version: str) -> dict:
    version = release_version.strip()
    if not version:
        raise StorageKnowledgeProductError("KNOWLEDGE_RELEASE_VERSION_REQUIRED")
    if version.lower() == "latest":
        raise StorageKnowledgeProductError("FLOATING_RELEASE_VERSION_FORBIDDEN")

    repo = repository()
    manifest = KnowledgeReleaseService(repo).build(
        version,
        created_at=datetime.now(timezone.utc),
    )
    source = repo.resolve(f"knowledge/production/releases/{version}")
    root = knowledge_release_root()
    candidates = root / "candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    target = candidates / version

    if target.exists():
        existing_manifest = target / "release_manifest.json"
        try:
            existing = json.loads(existing_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageKnowledgeProductError(
                "KNOWLEDGE_RELEASE_CANDIDATE_INVALID"
            ) from exc
        if str(existing.get("snapshot_hash") or "") != str(
            manifest.snapshot_hash or ""
        ):
            raise StorageKnowledgeProductError(
                "KNOWLEDGE_RELEASE_CANDIDATE_CONFLICT"
            )
    else:
        shutil.copytree(source, target)

    return {
        "status": "PENDING_PROMOTION",
        "knowledge_release_version": manifest.knowledge_release_version,
        "snapshot_hash": manifest.snapshot_hash,
        "object_count": manifest.object_count,
        "evidence_count": manifest.evidence_count,
        "source_reference_count": manifest.source_reference_count,
        "promotion_required": True,
    }


def _promotion_binding(
    release_version: str,
    *,
    approved_by: str,
    approved_at: datetime,
) -> dict[str, Any]:
    actor = approved_by.strip()
    if not actor:
        raise StorageKnowledgeProductError(
            "KNOWLEDGE_RELEASE_APPROVER_REQUIRED"
        )
    binding = _default_binding_template()
    binding["knowledge_release_version"] = release_version
    binding["latest_floating_dependency"] = False
    binding["storage_self_publish"] = False
    binding["compatibility_status"] = "PASS"
    binding["promotion"] = {
        "approved_by": actor,
        "approved_at": approved_at.isoformat(),
        "mode": "EXPLICIT_CONTROLLED_BINDING",
    }
    return binding


def promote_release_candidate(
    release_version: str,
    *,
    approved_by: str,
) -> dict:
    version = release_version.strip()
    if not version:
        raise StorageKnowledgeProductError("KNOWLEDGE_RELEASE_VERSION_REQUIRED")

    root = knowledge_release_root()
    candidate = root / "candidates" / version
    if not candidate.is_dir():
        raise StorageKnowledgeProductError(
            "KNOWLEDGE_RELEASE_CANDIDATE_NOT_FOUND"
        )

    try:
        manifest = json.loads(
            (candidate / "release_manifest.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageKnowledgeProductError(
            "KNOWLEDGE_RELEASE_CANDIDATE_INVALID"
        ) from exc

    approved_at = datetime.now(timezone.utc)
    binding = _promotion_binding(
        version,
        approved_by=approved_by,
        approved_at=approved_at,
    )
    try:
        validate_release_binding(binding, release_manifest=manifest)
    except Exception as exc:
        raise StorageKnowledgeProductError(
            f"KNOWLEDGE_RELEASE_BINDING_INVALID:{exc}"
        ) from exc

    current = root / "current"
    approved = root / "approved"
    approved.mkdir(parents=True, exist_ok=True)
    staged = root / f".next-{uuid4().hex}"
    backup = root / f".previous-{uuid4().hex}"

    shutil.copytree(candidate, staged)
    (staged / "release_binding.json").write_text(
        json.dumps(binding, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if current.is_dir():
        try:
            current_manifest = json.loads(
                (current / "release_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            current_version = str(
                current_manifest.get("knowledge_release_version") or ""
            ).strip()
        except (OSError, json.JSONDecodeError):
            current_version = ""
        if current_version:
            archive = approved / current_version
            if not archive.exists():
                shutil.copytree(current, archive)
                local_binding = archive / "release_binding.json"
                if not local_binding.is_file():
                    local_binding.write_text(
                        json.dumps(
                            _default_binding_template(),
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )

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
        "knowledge_release_version": version,
        "snapshot_hash": manifest.get("snapshot_hash"),
        "object_count": manifest.get("object_count", 0),
        "evidence_count": manifest.get("evidence_count", 0),
        "source_reference_count": manifest.get(
            "source_reference_count", 0
        ),
        "binding_mode": "EXPLICIT_CONTROLLED_BINDING",
        "approved_by": approved_by.strip(),
        "approved_at": approved_at.isoformat(),
    }


def build_and_activate_release(release_version: str) -> dict:
    """Backward-compatible safe wrapper.

    Historical callers used this name to build and immediately replace the
    active release. New Storage knowledge production must not half-switch the
    product before an explicit reviewed binding exists, so this now builds an
    immutable release candidate only.
    """
    return build_release_candidate(release_version)
