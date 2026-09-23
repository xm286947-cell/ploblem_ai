from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from repositories import JsonArtifactRepository

from .candidate import (
    KnowledgeCandidateError,
    KnowledgeCandidateService,
    business_source_ref_key,
)
from .models import (
    BusinessCandidateInput,
    KnowledgeCandidate,
)


class BusinessCandidateIntakeError(RuntimeError):
    """Stable Candidate Intake error for business-system submissions."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BusinessCandidateIntakeService:
    """Normalize business discoveries into the shared KnowledgeCandidate store."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository
        self.candidates = KnowledgeCandidateService(repository)

    def intake(
        self,
        payload: BusinessCandidateInput | dict[str, Any],
    ) -> KnowledgeCandidate:
        try:
            request = (
                payload
                if isinstance(payload, BusinessCandidateInput)
                else BusinessCandidateInput.model_validate(payload)
            )
        except ValidationError as exc:
            raise BusinessCandidateIntakeError(
                "CANDIDATE_CONTRACT_INVALID"
            ) from exc

        canonical_source_ref = business_source_ref_key(
            request.business_source_type.value,
            request.business_source_id,
            request.business_source_version,
        )
        source_refs = list(
            dict.fromkeys([*request.source_refs, canonical_source_ref])
        )

        candidate = KnowledgeCandidate(
            candidate_id=request.candidate_id,
            candidate_source_type="BUSINESS",
            business_source_type=request.business_source_type,
            business_source_id=request.business_source_id,
            business_source_version=request.business_source_version,
            object_type=request.object_type,
            title=request.title,
            summary=request.summary,
            content=request.content,
            device_type=request.device_type,
            scope=request.scope,
            conditions=request.conditions,
            limitations=request.limitations,
            tags=request.tags,
            evidence_refs=request.evidence_refs,
            source_refs=source_refs,
            extraction_version=None,
            confidence=request.confidence,
            status="CANDIDATE",
            producer=request.producer,
            contract_version=request.contract_version,
            created_at=request.created_at,
            metadata={
                **request.metadata,
                "business_provenance": {
                    "source_type": request.business_source_type.value,
                    "source_id": request.business_source_id,
                    "source_version": request.business_source_version,
                },
            },
        )

        try:
            return self.candidates.save_candidate(candidate)
        except KnowledgeCandidateError as exc:
            raise BusinessCandidateIntakeError(exc.code) from exc
