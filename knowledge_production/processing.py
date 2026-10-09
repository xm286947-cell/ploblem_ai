from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from repositories import JsonArtifactRepository

from .evaluation import KnowledgeEvaluationError, KnowledgeEvaluationService
from .models import (
    KnowledgeCandidate,
    KnowledgeEvaluation,
    KnowledgeObject,
    ReviewRecord,
    SourceDocument,
)
from .publish import (
    KnowledgePublishError,
    KnowledgePublishService,
    _STORAGE_SEMANTIC_ALLOWED_OBJECT_TYPES,
)
from .review import KnowledgeReviewError, KnowledgeReviewService


class KnowledgeProcessingError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class KnowledgeProcessingService:
    """Application facade for the Knowledge Processing UI.

    The UI uses only this facade. Repository layout and state-machine rules stay
    below the application boundary in existing domain/application services.
    """

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository
        self.evaluations = KnowledgeEvaluationService(repository)
        self.reviews = KnowledgeReviewService(repository)
        self.publisher = KnowledgePublishService(repository)

    def list_sources(self) -> list[SourceDocument]:
        root = self.repository.resolve("knowledge/source_documents")
        if not root.exists():
            return []
        result: list[SourceDocument] = []
        for path in sorted(root.glob("*/*/source_document.json")):
            payload = self.repository.load(path)
            if not isinstance(payload, dict):
                continue
            try:
                result.append(SourceDocument.model_validate(payload))
            except ValidationError:
                continue
        return result

    def list_candidates(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in self.repository.list("knowledge/production/candidates"):
            payload = self.repository.load(path)
            if not isinstance(payload, dict):
                continue
            try:
                candidate = KnowledgeCandidate.model_validate(payload)
            except ValidationError:
                continue
            detail = self._candidate_summary(candidate)
            items.append(detail)
        return sorted(items, key=lambda item: item["candidate_id"])

    def get_candidate_detail(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._load_candidate(candidate_id)
        evaluations = self._list_evaluations(candidate_id)
        reviews = self._list_reviews(candidate_id)
        evidence = []
        for evidence_id in candidate.evidence_refs:
            payload = self.repository.load(
                f"knowledge/production/evidence/{evidence_id}.json"
            )
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "payload": payload,
                    "available": isinstance(payload, dict),
                }
            )
        published = [
            obj
            for obj in self.list_published()
            if obj.candidate_id == candidate_id
        ]
        return {
            "candidate": candidate,
            "evaluations": evaluations,
            "latest_evaluation": evaluations[-1] if evaluations else None,
            "reviews": reviews,
            "latest_review": reviews[-1] if reviews else None,
            "evidences": evidence,
            "published": published[-1] if published else None,
            "storage_semantic_review": self._storage_semantic_review(candidate),
        }

    @staticmethod
    def _storage_semantic_review(candidate: KnowledgeCandidate) -> dict[str, Any] | None:
        """Use the exact Storage Publish contract's model-authorized options."""
        metadata = candidate.metadata or {}
        storage = metadata.get("storage_lifetime")
        if not isinstance(storage, dict) or not storage.get("model_driven_extraction"):
            return None
        bridge = metadata.get("storage_source_bridge")
        raw = (
            storage.get("semantic_class_review_options")
            or (bridge.get("semantic_class_candidates") if isinstance(bridge, dict) else None)
            or storage.get("semantic_class_candidates")
            or []
        )
        if not isinstance(raw, (list, tuple)):
            raw = []
        options = []
        for value in raw:
            name = str(value)
            allowed_types = _STORAGE_SEMANTIC_ALLOWED_OBJECT_TYPES.get(name)
            if allowed_types and name not in [item["name"] for item in options]:
                options.append({"name": name, "object_types": sorted(allowed_types)})
        return {
            "options": options,
            "object_types": sorted({
                item for option in options for item in option["object_types"]
            }),
            "status": storage.get("semantic_class_status") or "NEEDS_REVIEW",
        }

    def prepare_storage_semantic_edit(
        self, candidate_id: str, semantic_class: str, object_type: str
    ) -> dict[str, Any]:
        """Validate UI selection server-side; never accept arbitrary semantic tags."""
        candidate = self._load_candidate(candidate_id)
        review = self._storage_semantic_review(candidate)
        if review is None:
            raise KnowledgeProcessingError("STORAGE_SEMANTIC_REVIEW_NOT_APPLICABLE")
        option = next(
            (item for item in review["options"] if item["name"] == semantic_class),
            None,
        )
        if option is None:
            raise KnowledgeProcessingError("STORAGE_SEMANTIC_CLASS_INVALID")
        if object_type not in option["object_types"]:
            raise KnowledgeProcessingError("STORAGE_SEMANTIC_OBJECT_TYPE_INVALID")
        tags = [
            tag for tag in candidate.tags if not tag.startswith("storage-semantic:")
        ]
        tags.append(f"storage-semantic:{semantic_class}")
        return {"tags": tags, "object_type": object_type}

    def evaluate(self, candidate_id: str):
        try:
            return self.evaluations.evaluate_by_id(candidate_id)
        except KnowledgeEvaluationError as exc:
            raise KnowledgeProcessingError(exc.code) from exc

    def confirm(
        self,
        candidate_id: str,
        evaluation_id: str,
        *,
        reviewed_by: str,
        reviewed_at: datetime,
        review_note: str | None = None,
    ) -> ReviewRecord:
        try:
            return self.reviews.confirm(
                candidate_id,
                evaluation_id,
                reviewed_by=reviewed_by,
                reviewed_at=reviewed_at,
                review_note=review_note,
            )
        except KnowledgeReviewError as exc:
            raise KnowledgeProcessingError(exc.code) from exc

    def edit(
        self,
        candidate_id: str,
        evaluation_id: str,
        edits: dict[str, Any],
        *,
        reviewed_by: str,
        reviewed_at: datetime,
        review_note: str | None = None,
    ) -> ReviewRecord:
        try:
            return self.reviews.edit(
                candidate_id,
                evaluation_id,
                edits,
                reviewed_by=reviewed_by,
                reviewed_at=reviewed_at,
                review_note=review_note,
            )
        except KnowledgeReviewError as exc:
            raise KnowledgeProcessingError(exc.code) from exc

    def reject(
        self,
        candidate_id: str,
        evaluation_id: str,
        *,
        reviewed_by: str,
        reviewed_at: datetime,
        review_note: str | None = None,
    ) -> ReviewRecord:
        try:
            return self.reviews.reject(
                candidate_id,
                evaluation_id,
                reviewed_by=reviewed_by,
                reviewed_at=reviewed_at,
                review_note=review_note,
            )
        except KnowledgeReviewError as exc:
            raise KnowledgeProcessingError(exc.code) from exc

    def publish(
        self,
        candidate_id: str,
        *,
        published_by: str,
        published_at: datetime,
    ) -> KnowledgeObject:
        try:
            return self.publisher.publish(
                candidate_id,
                published_by=published_by,
                published_at=published_at,
            )
        except KnowledgePublishError as exc:
            raise KnowledgeProcessingError(exc.code) from exc

    def list_reviews(self) -> list[ReviewRecord]:
        root = self.repository.resolve("knowledge/production/reviews")
        if not root.exists():
            return []
        rows: list[ReviewRecord] = []
        for path in sorted(root.glob("*/*.json")):
            payload = self.repository.load(path)
            if not isinstance(payload, dict):
                continue
            try:
                rows.append(ReviewRecord.model_validate(payload))
            except ValidationError:
                continue
        return sorted(rows, key=lambda row: (row.reviewed_at, row.review_id))

    def list_published(self) -> list[KnowledgeObject]:
        rows: list[KnowledgeObject] = []
        for path in self.repository.list("knowledge/production/published"):
            payload = self.repository.load(path)
            if not isinstance(payload, dict):
                continue
            try:
                rows.append(KnowledgeObject.model_validate(payload))
            except ValidationError:
                continue
        return sorted(rows, key=lambda row: row.object_id)

    def _candidate_summary(self, candidate: KnowledgeCandidate) -> dict[str, Any]:
        evaluations = self._list_evaluations(candidate.candidate_id)
        reviews = self._list_reviews(candidate.candidate_id)
        return {
            "candidate_id": candidate.candidate_id,
            "object_type": candidate.object_type.value,
            "title": candidate.title,
            "source_type": candidate.candidate_source_type.value,
            "business_source_type": (
                candidate.business_source_type.value
                if candidate.business_source_type is not None
                else None
            ),
            "evidence_count": len(candidate.evidence_refs),
            "evaluation": evaluations[-1] if evaluations else None,
            "review": reviews[-1] if reviews else None,
        }

    def _load_candidate(self, candidate_id: str) -> KnowledgeCandidate:
        payload = self.repository.load(
            f"knowledge/production/candidates/{candidate_id}.json"
        )
        if not isinstance(payload, dict):
            raise KnowledgeProcessingError("CANDIDATE_NOT_FOUND")
        try:
            return KnowledgeCandidate.model_validate(payload)
        except ValidationError as exc:
            raise KnowledgeProcessingError(
                "KNOWLEDGE_CONTRACT_INVALID"
            ) from exc

    def _list_evaluations(self, candidate_id: str):
        rows = []
        paths = self.repository.list(
            f"knowledge/production/evaluations/{candidate_id}"
        )
        paths = sorted(paths, key=lambda path: (path.stat().st_mtime_ns, path.name))
        for path in paths:
            payload = self.repository.load(path)
            if not isinstance(payload, dict):
                continue
            try:
                rows.append(KnowledgeEvaluation.model_validate(payload))
            except ValidationError:
                continue
        return rows

    def _list_reviews(self, candidate_id: str) -> list[ReviewRecord]:
        rows: list[ReviewRecord] = []
        for path in self.repository.list(
            f"knowledge/production/reviews/{candidate_id}"
        ):
            payload = self.repository.load(path)
            if not isinstance(payload, dict):
                continue
            try:
                rows.append(ReviewRecord.model_validate(payload))
            except ValidationError:
                continue
        return sorted(rows, key=lambda row: (row.reviewed_at, row.review_id))
