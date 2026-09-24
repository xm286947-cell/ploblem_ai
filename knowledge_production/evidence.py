from __future__ import annotations

import hashlib
from typing import Any

from pydantic import ValidationError

from repositories import JsonArtifactRepository
from runtime.contracts import EvidenceLocator, EvidenceReference, SourceRef

from .public_contracts import (
    KnowledgeEvidenceInput,
    KnowledgeEvidenceResponse,
)


class KnowledgeEvidenceIntakeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BusinessEvidenceIntakeService:
    """Public business-evidence intake without business field interpretation."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository

    def intake(
        self,
        payload: KnowledgeEvidenceInput | dict[str, Any],
    ) -> KnowledgeEvidenceResponse:
        try:
            request = (
                payload
                if isinstance(payload, KnowledgeEvidenceInput)
                else KnowledgeEvidenceInput.model_validate(payload)
            )
        except ValidationError as exc:
            raise KnowledgeEvidenceIntakeError(
                "EVIDENCE_CONTRACT_INVALID"
            ) from exc

        if request.source_text is not None:
            actual_hash = hashlib.sha256(
                request.source_text.encode("utf-8")
            ).hexdigest()
            if actual_hash != request.content_hash:
                raise KnowledgeEvidenceIntakeError(
                    "EVIDENCE_CONTENT_HASH_MISMATCH"
                )

        locator_type = "BUSINESS_RECORD"
        if request.page is not None:
            locator_type = "PAGE"
        elif request.section:
            locator_type = "SECTION"

        locator_value = {
            "source_ref": request.source_ref,
            "page": request.page,
            "section": request.section,
            "paragraph": request.paragraph,
            "content_hash": request.content_hash,
        }
        evidence = EvidenceReference(
            evidence_id=request.evidence_id,
            source=SourceRef(
                source_id=request.source_document_id,
                source_type=request.source_type,
                revision=request.source_revision or f"R{request.revision}",
                content_hash=request.content_hash,
                fingerprint=(
                    f"{request.domain}:{request.source_document_id}:"
                    f"{request.source_revision or request.revision}:"
                    f"{request.content_hash}"
                ),
                uri=request.source_ref,
                metadata={
                    "domain": request.domain,
                    "contract_version": request.contract_version,
                    **request.metadata,
                },
            ),
            locator=EvidenceLocator(
                type=locator_type,
                value={
                    key: value
                    for key, value in locator_value.items()
                    if value is not None
                },
            ),
            excerpt=request.source_text,
            metadata={
                "evidence_status": "BOUND",
                "domain": request.domain,
                "public_contract_version": request.contract_version,
                "revision": request.revision,
            },
        )
        path = (
            "knowledge/production/evidence/"
            f"{request.evidence_id}.json"
        )
        stored = evidence.model_dump(mode="json")
        existing = self.repository.load(path)
        if existing is not None and existing != stored:
            raise KnowledgeEvidenceIntakeError("EVIDENCE_ID_CONFLICT")
        self.repository.save(path, stored)

        return KnowledgeEvidenceResponse(
            evidence_id=request.evidence_id,
            source_document_id=request.source_document_id,
            domain=request.domain,
            source_type=request.source_type,
            source_ref=request.source_ref,
            source_revision=request.source_revision,
            page=request.page,
            section=request.section,
            paragraph=request.paragraph,
            source_text=request.source_text,
            content_hash=request.content_hash,
            revision=request.revision,
        )
