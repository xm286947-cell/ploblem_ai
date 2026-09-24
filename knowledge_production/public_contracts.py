from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


KNOWLEDGE_EVIDENCE_CONTRACT_VERSION = "knowledge-evidence/v1"
KNOWLEDGE_REVIEW_CONTRACT_VERSION = "knowledge-review/v1"
KNOWLEDGE_PUBLISH_CONTRACT_VERSION = "knowledge-publish/v1"


class PublicContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeEvidenceInput(PublicContractModel):
    evidence_id: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_revision: str | None = None
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    paragraph: str | None = None
    source_text: str | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(default=1, ge=1)
    contract_version: str = Field(
        default=KNOWLEDGE_EVIDENCE_CONTRACT_VERSION,
        pattern=r"^knowledge-evidence/v1$",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeEvidenceResponse(PublicContractModel):
    evidence_id: str
    source_document_id: str
    domain: str
    source_type: str
    source_ref: str
    source_revision: str | None = None
    page: int | None = None
    section: str | None = None
    paragraph: str | None = None
    source_text: str | None = None
    content_hash: str
    revision: int
    contract_version: str = KNOWLEDGE_EVIDENCE_CONTRACT_VERSION


class KnowledgeCandidateIntakeRequest(PublicContractModel):
    candidate_id: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    object_type: str = Field(min_length=1)
    structured_content: dict[str, Any]
    evidence_refs: list[str] = Field(default_factory=list)
    status: Literal["PENDING_REVIEW"] = "PENDING_REVIEW"
    created_at: datetime
    revision: int = Field(default=1, ge=1)
    source_version: str | None = None
    producer: str = Field(default="BUSINESS_CANDIDATE_INTAKE", min_length=1)
    contract_version: str = Field(
        default="knowledge-candidate/v1",
        pattern=r"^knowledge-candidate/v1$",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_refs(self) -> "KnowledgeCandidateIntakeRequest":
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("duplicate evidence_refs")
        return self


class KnowledgeCandidateIntakeResponse(PublicContractModel):
    candidate_id: str
    source_document_id: str
    domain: str
    object_type: str
    structured_content: dict[str, Any]
    evidence_refs: list[str]
    status: Literal["PENDING_REVIEW"]
    created_at: datetime
    revision: int
    contract_version: str = "knowledge-candidate/v1"


class KnowledgeReviewRequest(PublicContractModel):
    candidate_id: str = Field(min_length=1)
    action: Literal["CONFIRM", "EDIT", "REJECT"]
    reviewer: str = Field(min_length=1)
    review_time: datetime
    review_comment: str | None = None
    confirmed_value: dict[str, Any] | None = None
    reviewed_content: dict[str, Any] | None = None
    revision: int = Field(default=1, ge=1)
    contract_version: str = Field(
        default=KNOWLEDGE_REVIEW_CONTRACT_VERSION,
        pattern=r"^knowledge-review/v1$",
    )

    @model_validator(mode="after")
    def validate_review_payload(self) -> "KnowledgeReviewRequest":
        if self.action == "EDIT" and self.reviewed_content is None:
            raise ValueError("EDIT requires reviewed_content")
        return self


class KnowledgeReviewResponse(PublicContractModel):
    review_id: str
    candidate_id: str
    review_status: Literal["CONFIRMED", "REJECTED"]
    reviewer: str
    review_time: datetime
    review_comment: str | None = None
    confirmed_value: dict[str, Any] | None = None
    reviewed_content: dict[str, Any] | None = None
    revision: int
    contract_version: str = KNOWLEDGE_REVIEW_CONTRACT_VERSION


class KnowledgePublishRequest(PublicContractModel):
    candidate_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    published_at: datetime
    revision: int = Field(default=1, ge=1)
    contract_version: str = Field(
        default=KNOWLEDGE_PUBLISH_CONTRACT_VERSION,
        pattern=r"^knowledge-publish/v1$",
    )


class PublishedKnowledgeObject(PublicContractModel):
    knowledge_id: str
    domain: str
    object_type: str
    content: dict[str, Any]
    candidate_ref: str
    evidence_refs: list[str]
    revision: int
    published_at: datetime
    status: str
    knowledge_release_version: str | None = None
    contract_version: str = "knowledge-object/v1"


class KnowledgePublishResponse(PublicContractModel):
    publish_status: Literal["PUBLISHED"]
    object: PublishedKnowledgeObject
    idempotency_key: str
    contract_version: str = KNOWLEDGE_PUBLISH_CONTRACT_VERSION


class PublicKnowledgeQuery(PublicContractModel):
    knowledge_release_version: str = Field(min_length=1)
    knowledge_ids: list[str] = Field(default_factory=list)
    domain: str | None = None
    object_type: str | None = None
    text: str | None = None
    contract_version: str = Field(
        default="knowledge-query/v1",
        pattern=r"^knowledge-query/v1$",
    )


class PublicKnowledgeQueryResult(PublicContractModel):
    knowledge_release_version: str
    objects: list[PublishedKnowledgeObject] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    contract_version: str = "knowledge-query/v1"
