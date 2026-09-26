from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from repositories import JsonArtifactRepository

from .candidate import (
    KnowledgeCandidateError,
    KnowledgeCandidateService,
    business_source_ref_key,
)
from .evaluation import KnowledgeEvaluationService
from .evidence import BusinessEvidenceIntakeService
from .models import (
    BusinessSourceType,
    KnowledgeCandidate,
    KnowledgeObject,
    ReleasedKnowledgeObject,
)
from .public_contracts import (
    KnowledgeCandidateIntakeRequest,
    KnowledgeCandidateIntakeResponse,
    KnowledgeEvidenceInput,
    KnowledgeEvidenceResponse,
    KnowledgePublishRequest,
    KnowledgePublishResponse,
    KnowledgeReviewRequest,
    KnowledgeReviewResponse,
    PublicKnowledgeQuery,
    PublicKnowledgeQueryResult,
    PublishedKnowledgeObject,
)
from .publish import KnowledgePublishError, KnowledgePublishService
from .query import KnowledgeQueryError, KnowledgeQueryService
from .review import KnowledgeReviewError, KnowledgeReviewService


class PublicKnowledgeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_content(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except Exception as exc:
        raise PublicKnowledgeError(
            "PUBLIC_KNOWLEDGE_CONTENT_INVALID"
        ) from exc
    if not isinstance(parsed, dict):
        raise PublicKnowledgeError("PUBLIC_KNOWLEDGE_CONTENT_INVALID")
    return parsed


def _business_source_type(domain: str) -> BusinessSourceType:
    normalized = domain.strip().upper()
    try:
        return BusinessSourceType(normalized)
    except ValueError:
        return BusinessSourceType.OTHER


class PublicKnowledgeService:
    """Public Contract facade over the existing Knowledge Production core."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository
        self.evidence = BusinessEvidenceIntakeService(repository)
        self.candidates = KnowledgeCandidateService(repository)
        self.evaluations = KnowledgeEvaluationService(repository)
        self.reviews = KnowledgeReviewService(repository)
        self.publisher = KnowledgePublishService(repository)
        self.query_service = KnowledgeQueryService(repository)

    def intake_evidence(
        self,
        payload: KnowledgeEvidenceInput | dict[str, Any],
    ) -> KnowledgeEvidenceResponse:
        try:
            return self.evidence.intake(payload)
        except Exception as exc:
            code = getattr(exc, "code", "EVIDENCE_INTAKE_FAILED")
            raise PublicKnowledgeError(code) from exc

    def intake_candidate(
        self,
        payload: KnowledgeCandidateIntakeRequest | dict[str, Any],
    ) -> KnowledgeCandidateIntakeResponse:
        try:
            request = (
                payload
                if isinstance(payload, KnowledgeCandidateIntakeRequest)
                else KnowledgeCandidateIntakeRequest.model_validate(payload)
            )
        except ValidationError as exc:
            raise PublicKnowledgeError(
                "CANDIDATE_CONTRACT_INVALID"
            ) from exc

        source_type = _business_source_type(request.domain)
        source_version = request.source_version or f"R{request.revision}"
        source_ref = business_source_ref_key(
            source_type.value,
            request.source_document_id,
            source_version,
        )
        candidate = KnowledgeCandidate(
            candidate_id=request.candidate_id,
            candidate_source_type="BUSINESS",
            business_source_type=source_type,
            business_source_id=request.source_document_id,
            business_source_version=source_version,
            object_type="FACT",
            title=f"{request.domain}:{request.candidate_id}",
            content=_canonical_json(request.structured_content),
            scope=[request.domain],
            tags=[
                request.domain.lower(),
                f"public_object_type:{request.object_type}",
            ],
            evidence_refs=request.evidence_refs,
            source_refs=[source_ref],
            confidence=None,
            status="CANDIDATE",
            producer=request.producer,
            contract_version=request.contract_version,
            created_at=request.created_at,
            metadata={
                **request.metadata,
                "public_contract": {
                    "domain": request.domain,
                    "object_type": request.object_type,
                    "source_document_id": request.source_document_id,
                    "revision": request.revision,
                },
            },
        )
        try:
            stored = self.candidates.save_candidate(candidate)
        except KnowledgeCandidateError as exc:
            raise PublicKnowledgeError(exc.code) from exc

        return KnowledgeCandidateIntakeResponse(
            candidate_id=stored.candidate_id,
            source_document_id=request.source_document_id,
            domain=request.domain,
            object_type=request.object_type,
            structured_content=request.structured_content,
            evidence_refs=stored.evidence_refs,
            status="PENDING_REVIEW",
            created_at=stored.created_at,
            revision=request.revision,
        )

    def review(
        self,
        payload: KnowledgeReviewRequest | dict[str, Any],
    ) -> KnowledgeReviewResponse:
        try:
            request = (
                payload
                if isinstance(payload, KnowledgeReviewRequest)
                else KnowledgeReviewRequest.model_validate(payload)
            )
        except ValidationError as exc:
            raise PublicKnowledgeError("REVIEW_CONTRACT_INVALID") from exc

        candidate = self._load_candidate(request.candidate_id)
        evaluation = self.evaluations.evaluate(candidate)
        original_content = _parse_content(candidate.content)

        try:
            if request.action == "CONFIRM":
                review = self.reviews.confirm(
                    candidate.candidate_id,
                    evaluation.evaluation_id,
                    reviewed_by=request.reviewer,
                    reviewed_at=request.review_time,
                    review_note=request.review_comment,
                )
                reviewed_content = (
                    request.confirmed_value
                    or request.reviewed_content
                    or original_content
                )
                if reviewed_content != original_content:
                    review = self.reviews.edit(
                        candidate.candidate_id,
                        evaluation.evaluation_id,
                        {"content": _canonical_json(reviewed_content)},
                        reviewed_by=request.reviewer,
                        reviewed_at=request.review_time,
                        review_note=request.review_comment,
                    )
            elif request.action == "EDIT":
                assert request.reviewed_content is not None
                reviewed_content = request.reviewed_content
                review = self.reviews.edit(
                    candidate.candidate_id,
                    evaluation.evaluation_id,
                    {"content": _canonical_json(reviewed_content)},
                    reviewed_by=request.reviewer,
                    reviewed_at=request.review_time,
                    review_note=request.review_comment,
                )
            else:
                reviewed_content = request.reviewed_content
                review = self.reviews.reject(
                    candidate.candidate_id,
                    evaluation.evaluation_id,
                    reviewed_by=request.reviewer,
                    reviewed_at=request.review_time,
                    review_note=request.review_comment,
                )
        except KnowledgeReviewError as exc:
            raise PublicKnowledgeError(exc.code) from exc

        response = KnowledgeReviewResponse(
            review_id=review.review_id,
            candidate_id=candidate.candidate_id,
            review_status=review.review_status.value,
            reviewer=request.reviewer,
            review_time=request.review_time,
            review_comment=request.review_comment,
            confirmed_value=(
                reviewed_content
                if review.review_status.value == "CONFIRMED"
                else None
            ),
            reviewed_content=reviewed_content,
            revision=request.revision,
        )
        self.repository.save(
            (
                "knowledge/production/public/reviews/"
                f"{candidate.candidate_id}/{review.review_id}.json"
            ),
            response.model_dump(mode="json"),
        )
        return response

    def publish(
        self,
        payload: KnowledgePublishRequest | dict[str, Any],
    ) -> KnowledgePublishResponse:
        try:
            request = (
                payload
                if isinstance(payload, KnowledgePublishRequest)
                else KnowledgePublishRequest.model_validate(payload)
            )
        except ValidationError as exc:
            raise PublicKnowledgeError("PUBLISH_CONTRACT_INVALID") from exc

        marker_path = (
            "knowledge/production/public/idempotency/publish/"
            f"{request.idempotency_key}.json"
        )
        marker = self.repository.load(marker_path)
        if isinstance(marker, dict):
            if marker.get("candidate_id") != request.candidate_id:
                raise PublicKnowledgeError("IDEMPOTENCY_KEY_CONFLICT")
            object_id = str(marker.get("knowledge_id") or "")
            if not object_id:
                raise PublicKnowledgeError("IDEMPOTENCY_RECORD_INVALID")
            obj = self._load_published_object(object_id)
            return KnowledgePublishResponse(
                publish_status="PUBLISHED",
                object=self._to_public_object(obj),
                idempotency_key=request.idempotency_key,
            )

        try:
            obj = self.publisher.publish(
                request.candidate_id,
                published_by=request.publisher,
                published_at=request.published_at,
            )
        except KnowledgePublishError as exc:
            raise PublicKnowledgeError(exc.code) from exc

        response = KnowledgePublishResponse(
            publish_status="PUBLISHED",
            object=self._to_public_object(obj),
            idempotency_key=request.idempotency_key,
        )
        self.repository.save(
            marker_path,
            {
                "candidate_id": request.candidate_id,
                "knowledge_id": obj.object_id,
                "object_version": obj.object_version,
                "idempotency_key": request.idempotency_key,
            },
        )
        return response

    def query(
        self,
        payload: PublicKnowledgeQuery | dict[str, Any],
    ) -> PublicKnowledgeQueryResult:
        try:
            request = (
                payload
                if isinstance(payload, PublicKnowledgeQuery)
                else PublicKnowledgeQuery.model_validate(payload)
            )
        except ValidationError as exc:
            raise PublicKnowledgeError("KNOWLEDGE_QUERY_INVALID") from exc

        try:
            result = self.query_service.query(
                {
                    "knowledge_release_version": request.knowledge_release_version,
                    "object_ids": request.knowledge_ids,
                }
            )
        except KnowledgeQueryError as exc:
            raise PublicKnowledgeError(exc.code) from exc

        objects: list[PublishedKnowledgeObject] = []
        for obj in result.objects:
            public = self._to_public_object(obj)
            if (
                request.candidate_refs
                and public.candidate_ref not in request.candidate_refs
            ):
                continue
            if request.domain and public.domain != request.domain:
                continue
            if request.object_type and public.object_type != request.object_type:
                continue
            if request.text:
                haystack = _canonical_json(public.content).lower()
                if request.text.strip().lower() not in haystack:
                    continue
            objects.append(public.model_copy(
                update={
                    "knowledge_release_version": (
                        request.knowledge_release_version
                    )
                }
            ))

        evidence_refs = list(
            dict.fromkeys(
                ref
                for obj in objects
                for ref in obj.evidence_refs
            )
        )
        return PublicKnowledgeQueryResult(
            knowledge_release_version=request.knowledge_release_version,
            objects=objects,
            evidence_refs=evidence_refs,
        )

    def get(
        self,
        knowledge_release_version: str,
        knowledge_id: str,
    ) -> PublishedKnowledgeObject:
        result = self.query(
            {
                "knowledge_release_version": knowledge_release_version,
                "knowledge_ids": [knowledge_id],
            }
        )
        if not result.objects:
            raise PublicKnowledgeError("KNOWLEDGE_OBJECT_NOT_FOUND")
        return result.objects[0]

    def resolve_evidence(
        self,
        knowledge_release_version: str,
        evidence_id: str,
    ) -> dict[str, Any]:
        try:
            return self.query_service.get_evidence(
                knowledge_release_version,
                evidence_id,
            )
        except KnowledgeQueryError as exc:
            raise PublicKnowledgeError(exc.code) from exc

    def _load_candidate(self, candidate_id: str) -> KnowledgeCandidate:
        payload = self.repository.load(
            f"knowledge/production/candidates/{candidate_id}.json",
            required=True,
        )
        assert payload is not None
        try:
            return KnowledgeCandidate.model_validate(payload)
        except ValidationError as exc:
            raise PublicKnowledgeError(
                "KNOWLEDGE_CONTRACT_INVALID"
            ) from exc

    def _load_published_object(self, object_id: str) -> KnowledgeObject:
        payload = self.repository.load(
            f"knowledge/production/published/{object_id}.json",
            required=True,
        )
        assert payload is not None
        try:
            return KnowledgeObject.model_validate(payload)
        except ValidationError as exc:
            raise PublicKnowledgeError(
                "KNOWLEDGE_CONTRACT_INVALID"
            ) from exc

    @staticmethod
    def _to_public_object(
        obj: KnowledgeObject | ReleasedKnowledgeObject,
    ) -> PublishedKnowledgeObject:
        metadata = obj.metadata or {}
        public = metadata.get("public_contract")
        if not isinstance(public, dict):
            public = {}
        domain = str(
            public.get("domain")
            or (
                obj.business_source_type.value
                if obj.business_source_type is not None
                else "UNKNOWN"
            )
        )
        object_type = str(
            public.get("object_type")
            or obj.object_type.value
        )
        revision = int(
            public.get("revision")
            or obj.object_version
        )
        return PublishedKnowledgeObject(
            knowledge_id=obj.object_id,
            domain=domain,
            object_type=object_type,
            content=_parse_content(obj.content),
            candidate_ref=obj.candidate_id,
            evidence_refs=obj.evidence_refs,
            revision=revision,
            published_at=obj.published_at,
            status=obj.status.value,
            knowledge_release_version=getattr(
                obj, "knowledge_release_version", None
            ),
        )
