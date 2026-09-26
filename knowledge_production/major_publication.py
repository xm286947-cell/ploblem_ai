"""Unified Knowledge consumer for the Major publication public contract.

This is the receiving adapter.  It translates the public Major contract into
the existing business-candidate and evidence intake services, then leaves
evaluation, human review, publish, object versioning, and release management
to the existing Knowledge pipeline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from pydantic import ValidationError

from repositories import JsonArtifactRepository
from services.major_knowledge_publication import (
    KNOWLEDGE_CANDIDATE_CONTRACT,
    MajorKnowledgePublicationContract,
    MajorKnowledgePublicationSubmission,
)

from .evidence import BusinessEvidenceIntakeService, KnowledgeEvidenceIntakeError
from .intake import BusinessCandidateIntakeError, BusinessCandidateIntakeService
from .models import BusinessCandidateInput, BusinessSourceType


class MajorPublicationIntakeError(RuntimeError):
    """Stable errors returned by the Unified Knowledge public consumer."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _payload_hash(publication: MajorKnowledgePublicationContract) -> str:
    payload = publication.model_dump(mode="json")
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class MajorPublicationIntakeAdapter:
    """Submit Major candidates through the existing Knowledge intake gate."""

    def __init__(self, repository: JsonArtifactRepository):
        self.repository = repository
        self.evidence = BusinessEvidenceIntakeService(repository)
        self.candidates = BusinessCandidateIntakeService(repository)

    def submit(
        self,
        publication: MajorKnowledgePublicationContract | dict[str, Any],
    ) -> MajorKnowledgePublicationSubmission:
        try:
            request = (
                publication
                if isinstance(publication, MajorKnowledgePublicationContract)
                else MajorKnowledgePublicationContract.model_validate(publication)
            )
        except ValidationError as exc:
            raise MajorPublicationIntakeError("PUBLICATION_CONTRACT_INVALID") from exc

        if request.contract_version != "major-knowledge-publication/v1":
            raise MajorPublicationIntakeError("KNOWLEDGE_CONTRACT_UNSUPPORTED")
        if request.producer != "MAJOR_ISSUE" or request.publication_status != "SUBMITTED":
            raise MajorPublicationIntakeError("PUBLICATION_CONTRACT_INVALID")

        record_path = (
            "knowledge/production/major_publications/"
            f"{request.idempotency_key}.json"
        )
        request_hash = _payload_hash(request)
        existing = self.repository.load(record_path)
        if existing is not None:
            if existing.get("payload_hash") != request_hash:
                raise MajorPublicationIntakeError("PUBLICATION_IDENTITY_CONFLICT")
            return MajorKnowledgePublicationSubmission(
                status="IDEMPOTENT_REPLAY",
                idempotency_key=request.idempotency_key,
                publication_revision=request.publication_revision,
                candidate_ids=list(existing.get("candidate_ids") or []),
                knowledge_release_version=existing.get("knowledge_release_version"),
            )

        evidence_by_id = {
            item.evidence_id: item for item in request.evidence_bindings
        }
        if set(request.evidence_refs) != set(evidence_by_id):
            raise MajorPublicationIntakeError("EVIDENCE_MISSING")

        for evidence in request.evidence_bindings:
            try:
                self.evidence.intake(
                    {
                        "evidence_id": evidence.evidence_id,
                        "source_document_id": evidence.source["source_id"],
                        "domain": "MAJOR_ISSUE",
                        "source_type": evidence.source["source_type"],
                        "source_ref": evidence.source_ref,
                        "source_revision": evidence.source["source_version"],
                        "section": str(evidence.locator.get("section") or "") or None,
                        "source_text": evidence.source_text or evidence.excerpt,
                        "content_hash": evidence.content_hash,
                        "revision": 1,
                        "contract_version": "knowledge-evidence/v1",
                        "metadata": {
                            "major_publication_contract": request.contract_version,
                            "major_case_ref": request.major_case_ref,
                            "major_confirmed_revision": request.major_confirmed_revision,
                            "publication_revision": request.publication_revision,
                            "common_evidence_contract": evidence.contract_version,
                            "evidence_anchor": evidence.locator.get("anchor"),
                        },
                    }
                )
            except KnowledgeEvidenceIntakeError as exc:
                raise MajorPublicationIntakeError(exc.code) from exc

        candidate_ids: list[str] = []
        now = datetime.now(timezone.utc)
        for candidate in request.knowledge_candidates:
            try:
                saved = self.candidates.intake(
                    BusinessCandidateInput(
                        candidate_id=candidate.candidate_id,
                        candidate_source_type="BUSINESS",
                        business_source_type=BusinessSourceType.MAJOR_ISSUE,
                        business_source_id=candidate.business_source_id,
                        business_source_version=candidate.business_source_version,
                        object_type=candidate.object_type,
                        title=candidate.title,
                        summary=candidate.summary,
                        content=candidate.content,
                        scope=candidate.scope,
                        conditions=candidate.conditions,
                        limitations=candidate.limitations,
                        tags=candidate.tags,
                        source_refs=candidate.source_refs,
                        evidence_refs=candidate.evidence_refs,
                        created_at=now,
                        producer=candidate.producer,
                        # The consumer keeps the existing internal candidate
                        # contract and records the Major contract in metadata.
                        contract_version=KNOWLEDGE_CANDIDATE_CONTRACT,
                        metadata={
                            "publication_contract_version": request.contract_version,
                            "publication_idempotency_key": request.idempotency_key,
                            "major_case_ref": request.major_case_ref,
                            "major_confirmed_revision": request.major_confirmed_revision,
                            "publication_revision": request.publication_revision,
                            "business_ref": request.source.business_ref,
                        },
                    )
                )
            except (BusinessCandidateIntakeError, ValidationError) as exc:
                code = getattr(exc, "code", "CANDIDATE_CONTRACT_INVALID")
                raise MajorPublicationIntakeError(str(code)) from exc
            candidate_ids.append(saved.candidate_id)

        self.repository.save(
            record_path,
            {
                "contract_version": request.contract_version,
                "idempotency_key": request.idempotency_key,
                "publication_revision": request.publication_revision,
                "payload_hash": request_hash,
                "candidate_ids": candidate_ids,
                "publication_status": request.publication_status,
                "knowledge_release_version": None,
            },
        )
        return MajorKnowledgePublicationSubmission(
            status="SUBMITTED",
            idempotency_key=request.idempotency_key,
            publication_revision=request.publication_revision,
            candidate_ids=candidate_ids,
        )


__all__ = ["MajorPublicationIntakeAdapter", "MajorPublicationIntakeError"]
