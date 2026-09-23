from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from repositories import JsonArtifactRepository

from .models import (
    KnowledgeQuery,
    KnowledgeQueryResult,
    KnowledgeReleaseManifest,
    KnowledgeSourceReference,
    ReleasedKnowledgeObject,
)


class KnowledgeQueryError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class KnowledgeQueryService:
    """Version-pinned consumer facade over immutable Knowledge Releases."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository

    def query(
        self,
        request: KnowledgeQuery | dict[str, Any],
    ) -> KnowledgeQueryResult:
        try:
            query = (
                request
                if isinstance(request, KnowledgeQuery)
                else KnowledgeQuery.model_validate(request)
            )
        except ValidationError as exc:
            raise KnowledgeQueryError("KNOWLEDGE_QUERY_INVALID") from exc

        base = (
            "knowledge/production/releases/"
            f"{query.knowledge_release_version}"
        )
        manifest_payload = self.repository.load(
            f"{base}/release_manifest.json"
        )
        if not isinstance(manifest_payload, dict):
            raise KnowledgeQueryError("KNOWLEDGE_RELEASE_NOT_FOUND")
        try:
            manifest = KnowledgeReleaseManifest.model_validate(
                manifest_payload
            )
        except ValidationError as exc:
            raise KnowledgeQueryError(
                "KNOWLEDGE_RELEASE_INVALID"
            ) from exc

        objects_payload = self.repository.load(f"{base}/knowledge_objects.json")
        evidence_payload = self.repository.load(f"{base}/evidences.json")
        source_payload = self.repository.load(f"{base}/source_references.json")
        if not isinstance(objects_payload, list):
            raise KnowledgeQueryError("KNOWLEDGE_RELEASE_INVALID")
        if not isinstance(evidence_payload, list):
            raise KnowledgeQueryError("KNOWLEDGE_RELEASE_INVALID")
        if not isinstance(source_payload, list):
            raise KnowledgeQueryError("KNOWLEDGE_RELEASE_INVALID")

        try:
            objects = [
                ReleasedKnowledgeObject.model_validate(item)
                for item in objects_payload
            ]
            sources = [
                KnowledgeSourceReference.model_validate(item)
                for item in source_payload
            ]
        except ValidationError as exc:
            raise KnowledgeQueryError("KNOWLEDGE_RELEASE_INVALID") from exc

        selected = [item for item in objects if self._matches(item, query)]
        evidence_by_id = {
            str(item.get("evidence_id") or ""): item
            for item in evidence_payload
            if isinstance(item, dict) and item.get("evidence_id")
        }
        source_by_ref = {item.source_ref: item for item in sources}

        evidence_ids: list[str] = []
        source_refs: list[str] = []
        gaps: list[str] = []
        for obj in selected:
            for evidence_id in obj.evidence_refs:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
                if evidence_id not in evidence_by_id:
                    gaps.append(f"EVIDENCE_MISSING:{evidence_id}")
            for source_ref in obj.source_refs:
                if source_ref not in source_refs:
                    source_refs.append(source_ref)
                if source_ref not in source_by_ref:
                    gaps.append(f"SOURCE_REFERENCE_MISSING:{source_ref}")

        evidences = [
            evidence_by_id[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id in evidence_by_id
        ]
        selected_sources = [
            source_by_ref[source_ref]
            for source_ref in source_refs
            if source_ref in source_by_ref
        ]
        return KnowledgeQueryResult(
            knowledge_release_version=manifest.knowledge_release_version,
            objects=selected,
            evidences=evidences,
            source_references=selected_sources,
            unknowns_or_gaps=list(dict.fromkeys(gaps)),
        )

    def get_evidence(
        self,
        knowledge_release_version: str,
        evidence_id: str,
    ) -> dict[str, Any]:
        result = self.query(
            {
                "knowledge_release_version": knowledge_release_version,
            }
        )
        for evidence in result.evidences:
            if evidence.get("evidence_id") == evidence_id:
                return evidence

        payload = self.repository.load(
            (
                "knowledge/production/releases/"
                f"{knowledge_release_version}/evidences.json"
            )
        )
        if not isinstance(payload, list):
            raise KnowledgeQueryError("KNOWLEDGE_RELEASE_NOT_FOUND")
        for evidence in payload:
            if (
                isinstance(evidence, dict)
                and evidence.get("evidence_id") == evidence_id
            ):
                return evidence
        raise KnowledgeQueryError("EVIDENCE_NOT_FOUND")

    def get_source_reference(
        self,
        knowledge_release_version: str,
        source_ref: str,
    ) -> KnowledgeSourceReference:
        payload = self.repository.load(
            (
                "knowledge/production/releases/"
                f"{knowledge_release_version}/source_references.json"
            )
        )
        if not isinstance(payload, list):
            raise KnowledgeQueryError("KNOWLEDGE_RELEASE_NOT_FOUND")
        for item in payload:
            if isinstance(item, dict) and item.get("source_ref") == source_ref:
                try:
                    return KnowledgeSourceReference.model_validate(item)
                except ValidationError as exc:
                    raise KnowledgeQueryError(
                        "KNOWLEDGE_RELEASE_INVALID"
                    ) from exc
        raise KnowledgeQueryError("SOURCE_REFERENCE_NOT_FOUND")

    @staticmethod
    def _matches(
        obj: ReleasedKnowledgeObject,
        query: KnowledgeQuery,
    ) -> bool:
        if query.object_ids and obj.object_id not in query.object_ids:
            return False
        if (
            query.object_types
            and obj.object_type not in query.object_types
        ):
            return False
        if query.device_types:
            device = (obj.device_type or "").lower()
            wanted = {item.lower() for item in query.device_types}
            if device not in wanted:
                return False
        if query.tags:
            tags = {item.lower() for item in obj.tags}
            if not {item.lower() for item in query.tags}.issubset(tags):
                return False
        if query.topic:
            topic = query.topic.strip().lower()
            haystack = "\n".join(
                [
                    obj.title,
                    obj.summary or "",
                    obj.content,
                    " ".join(obj.tags),
                    " ".join(obj.scope),
                ]
            ).lower()
            if topic not in haystack:
                return False
        return True
