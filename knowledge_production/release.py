from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from repositories import JsonArtifactRepository

from .models import (
    KnowledgeObject,
    KnowledgeObjectStatus,
    KnowledgeReleaseManifest,
    KnowledgeSourceReference,
    ReleasedKnowledgeObject,
)


class KnowledgeReleaseError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_release_version(value: str) -> str:
    result = value.strip()
    if not result or not re.fullmatch(r"[A-Za-z0-9._-]+", result):
        raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_VERSION_INVALID")
    return result


class KnowledgeReleaseService:
    """Create an immutable, Storage-independent Knowledge Release snapshot."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository

    def build(
        self,
        knowledge_release_version: str,
        *,
        created_at: datetime,
    ) -> KnowledgeReleaseManifest:
        release_version = _safe_release_version(knowledge_release_version)
        objects = self._active_objects(release_version)
        if not objects:
            raise KnowledgeReleaseError("NO_ACTIVE_KNOWLEDGE")

        evidences = self._collect_evidences(objects)
        sources = self._collect_sources(objects)

        object_payload = [item.model_dump(mode="json") for item in objects]
        evidence_payload = [evidences[key] for key in sorted(evidences)]
        source_payload = [
            sources[key].model_dump(mode="json") for key in sorted(sources)
        ]
        snapshot_material = {
            "knowledge_release_version": release_version,
            "objects": object_payload,
            "evidences": evidence_payload,
            "source_references": source_payload,
        }
        snapshot_hash = _sha256_bytes(_canonical_bytes(snapshot_material))

        base = f"knowledge/production/releases/{release_version}"
        existing = self.repository.load(f"{base}/release_manifest.json")
        if isinstance(existing, dict):
            manifest = KnowledgeReleaseManifest.model_validate(existing)
            if manifest.snapshot_hash != snapshot_hash:
                raise KnowledgeReleaseError(
                    "KNOWLEDGE_RELEASE_VERSION_CONFLICT"
                )
            self._ensure_package(release_version, manifest)
            return manifest

        files: dict[str, bytes] = {
            "knowledge_objects.json": _canonical_bytes({"objects": object_payload}),
            "evidences.json": _canonical_bytes({"evidences": evidence_payload}),
            "source_references.json": _canonical_bytes(
                {"source_references": source_payload}
            ),
        }
        migration_notes = (
            "No migration from chunk/retrieval contracts is implied. "
            "Consumers must use knowledge-query/v1 and pin a release version."
        )
        compatibility_notes = (
            "Compatible with knowledge-object/v1 and knowledge-candidate/v1. "
            "Historical Case remains owned by historical-case/v1; business "
            "sources are represented by stable references only."
        )
        files["MIGRATION_NOTES.md"] = (migration_notes + "\n").encode("utf-8")
        files["COMPATIBILITY_NOTES.md"] = (
            compatibility_notes + "\n"
        ).encode("utf-8")

        file_entries = [
            {
                "path": path,
                "sha256": _sha256_bytes(payload),
                "size": len(payload),
            }
            for path, payload in sorted(files.items())
        ]
        manifest = KnowledgeReleaseManifest(
            knowledge_release_version=release_version,
            created_at=created_at,
            snapshot_hash=snapshot_hash,
            object_count=len(objects),
            evidence_count=len(evidence_payload),
            source_reference_count=len(source_payload),
            files=file_entries,
            migration_notes=migration_notes,
            compatibility_notes=compatibility_notes,
        )
        files["release_manifest.json"] = _canonical_bytes(
            manifest.model_dump(mode="json")
        )

        release_root = self.repository.resolve(base)
        release_root.mkdir(parents=True, exist_ok=True)
        for path, payload in files.items():
            target = release_root / path
            target.write_bytes(payload)

        self._ensure_package(release_version, manifest)
        return manifest

    def _active_objects(
        self,
        release_version: str,
    ) -> list[ReleasedKnowledgeObject]:
        result: list[ReleasedKnowledgeObject] = []
        for path in self.repository.list("knowledge/production/published"):
            payload = self.repository.load(path)
            if not isinstance(payload, dict):
                continue
            try:
                obj = KnowledgeObject.model_validate(payload)
            except ValidationError as exc:
                raise KnowledgeReleaseError(
                    "KNOWLEDGE_CONTRACT_INVALID"
                ) from exc
            if obj.status != KnowledgeObjectStatus.ACTIVE:
                continue
            result.append(
                ReleasedKnowledgeObject(
                    **obj.model_dump(mode="python"),
                    knowledge_release_version=release_version,
                )
            )
        return sorted(result, key=lambda item: item.object_id)

    def _collect_evidences(
        self,
        objects: list[ReleasedKnowledgeObject],
    ) -> dict[str, dict[str, Any]]:
        evidences: dict[str, dict[str, Any]] = {}
        for obj in objects:
            for evidence_id in obj.evidence_refs:
                payload = self.repository.load(
                    f"knowledge/production/evidence/{evidence_id}.json"
                )
                if not isinstance(payload, dict):
                    raise KnowledgeReleaseError("EVIDENCE_MISSING")
                evidences[evidence_id] = payload
        return evidences

    def _collect_sources(
        self,
        objects: list[ReleasedKnowledgeObject],
    ) -> dict[str, KnowledgeSourceReference]:
        sources: dict[str, KnowledgeSourceReference] = {}
        for obj in objects:
            if obj.candidate_source_type.value == "BUSINESS":
                if (
                    obj.business_source_type is None
                    or not obj.business_source_id
                ):
                    raise KnowledgeReleaseError(
                        "BUSINESS_PROVENANCE_INVALID"
                    )
                version = obj.business_source_version or "UNVERSIONED"
                canonical = (
                    f"{obj.business_source_type.value}:"
                    f"{obj.business_source_id}@{version}"
                )
                sources[canonical] = KnowledgeSourceReference(
                    source_ref=canonical,
                    source_kind="BUSINESS_OBJECT",
                    source_id=obj.business_source_id,
                    source_version=obj.business_source_version,
                    source_type=obj.business_source_type.value,
                )

            for source_ref in obj.source_refs:
                if source_ref in sources:
                    continue
                if ":" in source_ref.split("@", 1)[0]:
                    prefix, rest = source_ref.split(":", 1)
                    source_id, version = self._split_ref(rest)
                    sources[source_ref] = KnowledgeSourceReference(
                        source_ref=source_ref,
                        source_kind="BUSINESS_OBJECT",
                        source_id=source_id,
                        source_version=(
                            None if version == "UNVERSIONED" else version
                        ),
                        source_type=prefix,
                    )
                    continue

                source_id, version = self._split_ref(source_ref)
                manifest = self.repository.load(
                    (
                        f"knowledge/source_documents/{source_id}/"
                        f"{version}/source_document.json"
                    )
                )
                if not isinstance(manifest, dict):
                    raise KnowledgeReleaseError("SOURCE_UNAVAILABLE")
                sources[source_ref] = KnowledgeSourceReference(
                    source_ref=source_ref,
                    source_kind="EXTERNAL_SOURCE_DOCUMENT",
                    source_id=source_id,
                    source_version=version,
                    source_type=str(
                        manifest.get("document_type") or "DOCUMENT"
                    ),
                    title=manifest.get("title"),
                    publisher=manifest.get("publisher"),
                    official_url=manifest.get("official_url"),
                    source_ref_uri=manifest.get("source_ref"),
                    content_hash=manifest.get("content_hash"),
                )
        return sources

    @staticmethod
    def _split_ref(value: str) -> tuple[str, str]:
        if "@" not in value:
            raise KnowledgeReleaseError("SOURCE_TRACEABILITY_INVALID")
        source_id, version = value.rsplit("@", 1)
        if not source_id or not version:
            raise KnowledgeReleaseError("SOURCE_TRACEABILITY_INVALID")
        return source_id, version

    def _ensure_package(
        self,
        release_version: str,
        manifest: KnowledgeReleaseManifest,
    ) -> Path:
        base = self.repository.resolve(
            f"knowledge/production/releases/{release_version}"
        )
        package = self.repository.resolve(
            (
                "knowledge/production/release_packages/"
                f"knowledge-release-{release_version}.zip"
            )
        )
        package.parent.mkdir(parents=True, exist_ok=True)
        if package.exists():
            return package
        with zipfile.ZipFile(
            package, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for name in (
                "release_manifest.json",
                "knowledge_objects.json",
                "evidences.json",
                "source_references.json",
                "MIGRATION_NOTES.md",
                "COMPATIBILITY_NOTES.md",
            ):
                source = base / name
                if not source.is_file():
                    raise KnowledgeReleaseError(
                        "KNOWLEDGE_RELEASE_INCOMPLETE"
                    )
                archive.write(source, arcname=name)
        return package
