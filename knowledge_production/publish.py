from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from repositories import JsonArtifactRepository

from .evaluation import KnowledgeEvaluationService
from .models import (
    ConflictStatus,
    DuplicateStatus,
    EvidenceValidationStatus,
    KnowledgeObject,
    KnowledgeObjectStatus,
    ReviewStatus,
)
from .review import KnowledgeReviewError, KnowledgeReviewService


class KnowledgePublishError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class KnowledgePublishService:
    """Fail-closed Candidate -> reviewed snapshot -> KnowledgeObject publisher."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository
        self.reviews = KnowledgeReviewService(repository)
        self.evaluations = KnowledgeEvaluationService(repository)

    def publish(
        self,
        candidate_id: str,
        *,
        published_by: str,
        published_at: datetime,
    ) -> KnowledgeObject:
        actor = published_by.strip()
        if not actor:
            raise KnowledgePublishError("PUBLISHER_REQUIRED")

        review = self.reviews.latest_review(candidate_id)
        if review is None or review.review_status != ReviewStatus.CONFIRMED:
            raise KnowledgePublishError("PUBLISH_NOT_CONFIRMED")

        try:
            candidate = self.reviews.load_effective_candidate(review)
        except KnowledgeReviewError as exc:
            raise KnowledgePublishError(exc.code) from exc

        object_id = self._object_id(candidate.candidate_id)
        current = self._load_current(object_id)
        if current is not None and self._same_knowledge_material(current, candidate):
            return current

        evaluation = self.evaluations.evaluate(
            candidate,
            exclude_object_ids={object_id} if current is not None else None,
        )
        self._enforce_gate(evaluation)

        version = 1 if current is None else current.object_version + 1
        obj = KnowledgeObject(
            object_id=object_id,
            object_version=version,
            status=KnowledgeObjectStatus.ACTIVE,
            candidate_id=candidate.candidate_id,
            review_id=review.review_id,
            evaluation_id=evaluation.evaluation_id,
            candidate_source_type=candidate.candidate_source_type,
            business_source_type=candidate.business_source_type,
            business_source_id=candidate.business_source_id,
            business_source_version=candidate.business_source_version,
            object_type=candidate.object_type,
            title=candidate.title,
            summary=candidate.summary,
            content=candidate.content,
            device_type=candidate.device_type,
            scope=candidate.scope,
            conditions=candidate.conditions,
            limitations=candidate.limitations,
            tags=candidate.tags,
            evidence_refs=candidate.evidence_refs,
            source_refs=candidate.source_refs,
            producer=candidate.producer,
            published_by=actor,
            published_at=published_at,
        )
        self._commit_version(obj)
        return obj

    def transition_status(
        self,
        object_id: str,
        status: KnowledgeObjectStatus | str,
        *,
        changed_by: str,
        changed_at: datetime,
    ) -> KnowledgeObject:
        actor = changed_by.strip()
        if not actor:
            raise KnowledgePublishError("PUBLISHER_REQUIRED")
        current = self._load_current(object_id)
        if current is None:
            raise KnowledgePublishError("KNOWLEDGE_OBJECT_NOT_FOUND")

        target = KnowledgeObjectStatus(status)
        allowed = {
            KnowledgeObjectStatus.ACTIVE: {
                KnowledgeObjectStatus.STALE,
                KnowledgeObjectStatus.DEPRECATED,
            },
            KnowledgeObjectStatus.STALE: {
                KnowledgeObjectStatus.DEPRECATED,
            },
            KnowledgeObjectStatus.DEPRECATED: set(),
        }
        if target == current.status:
            return current
        if target not in allowed[current.status]:
            raise KnowledgePublishError("KNOWLEDGE_STATE_TRANSITION_INVALID")

        updated = current.model_copy(
            update={
                "object_version": current.object_version + 1,
                "status": target,
                "published_by": actor,
                "published_at": changed_at,
            }
        )
        self._commit_version(updated)
        return updated

    def _enforce_gate(self, evaluation) -> None:
        if not evaluation.contract_valid or not evaluation.candidate_complete:
            raise KnowledgePublishError("KNOWLEDGE_CONTRACT_INVALID")
        if evaluation.evidence_status != EvidenceValidationStatus.VALID:
            raise KnowledgePublishError("EVIDENCE_MISSING")
        if not evaluation.source_valid:
            raise KnowledgePublishError("SOURCE_UNAVAILABLE")
        if not evaluation.scope_valid:
            raise KnowledgePublishError("KNOWLEDGE_CONTRACT_INVALID")
        if evaluation.conflict_status != ConflictStatus.NONE:
            raise KnowledgePublishError("UNRESOLVED_CONFLICT")
        if evaluation.duplicate_status != DuplicateStatus.NEW:
            raise KnowledgePublishError("DUPLICATE_KNOWLEDGE")
        if not evaluation.publish_readiness:
            raise KnowledgePublishError("PUBLISH_GATE_FAILED")

    def _commit_version(self, obj: KnowledgeObject) -> None:
        payload = obj.model_dump(mode="json")
        history_path = (
            f"knowledge/production/published_versions/{obj.object_id}/"
            f"v{obj.object_version:06d}.json"
        )
        existing_history = self.repository.load(history_path)
        if existing_history is not None and existing_history != payload:
            raise KnowledgePublishError("OBJECT_VERSION_CONFLICT")
        self.repository.save(history_path, payload)

        current_path = f"knowledge/production/published/{obj.object_id}.json"
        current = self.repository.load(current_path)
        if current is not None:
            current_version = int(current.get("object_version") or 0)
            if current_version >= obj.object_version and current != payload:
                raise KnowledgePublishError("OBJECT_VERSION_CONFLICT")
        self.repository.save(current_path, payload)

    def _load_current(self, object_id: str) -> KnowledgeObject | None:
        payload = self.repository.load(
            f"knowledge/production/published/{object_id}.json"
        )
        if not isinstance(payload, dict):
            return None
        try:
            return KnowledgeObject.model_validate(payload)
        except ValidationError as exc:
            raise KnowledgePublishError(
                "KNOWLEDGE_CONTRACT_INVALID"
            ) from exc

    @staticmethod
    def _object_id(candidate_id: str) -> str:
        digest = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()
        return "KO-" + digest[:24]

    @staticmethod
    def _same_knowledge_material(
        current: KnowledgeObject,
        candidate,
    ) -> bool:
        current_material = {
            "candidate_id": current.candidate_id,
            "candidate_source_type": current.candidate_source_type.value,
            "business_source_type": (
                current.business_source_type.value
                if current.business_source_type is not None
                else None
            ),
            "business_source_id": current.business_source_id,
            "business_source_version": current.business_source_version,
            "object_type": current.object_type.value,
            "title": current.title,
            "summary": current.summary,
            "content": current.content,
            "device_type": current.device_type,
            "scope": current.scope,
            "conditions": current.conditions,
            "limitations": current.limitations,
            "tags": current.tags,
            "evidence_refs": current.evidence_refs,
            "source_refs": current.source_refs,
            "producer": current.producer,
        }
        candidate_material = {
            "candidate_id": candidate.candidate_id,
            "candidate_source_type": candidate.candidate_source_type.value,
            "business_source_type": (
                candidate.business_source_type.value
                if candidate.business_source_type is not None
                else None
            ),
            "business_source_id": candidate.business_source_id,
            "business_source_version": candidate.business_source_version,
            "object_type": candidate.object_type.value,
            "title": candidate.title,
            "summary": candidate.summary,
            "content": candidate.content,
            "device_type": candidate.device_type,
            "scope": candidate.scope,
            "conditions": candidate.conditions,
            "limitations": candidate.limitations,
            "tags": candidate.tags,
            "evidence_refs": candidate.evidence_refs,
            "source_refs": candidate.source_refs,
            "producer": candidate.producer,
        }
        return current_material == candidate_material
