from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


KNOWLEDGE_CANDIDATE_CONTRACT_VERSION = "knowledge-candidate/v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RetrievalStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PARSED = "PARSED"
    FAILED = "FAILED"


class SourceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    DEPRECATED = "DEPRECATED"


class SourceDocument(StrictModel):
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    title: str = Field(min_length=1)
    version: str | None = None
    revision: str | None = None
    document_type: str = "PDF"
    official_url: str | None = None
    source_ref: str | None = None
    local_cache_ref: str = Field(min_length=1)
    original_file_name: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: str = "en"
    retrieval_status: RetrievalStatus = RetrievalStatus.RECEIVED
    source_status: SourceStatus = SourceStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StructuredTextBlock(StrictModel):
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    page: int = Field(ge=1)
    section: str | None = None
    source_text: str
    source_anchor: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class StructuredDocument(StrictModel):
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    parse_status: Literal["PARSED", "FAILED"]
    page_count: int = Field(ge=0)
    blocks: list[StructuredTextBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class KnowledgeObjectType(str, Enum):
    FACT = "FACT"
    CONCEPT = "CONCEPT"
    SOLUTION = "SOLUTION"
    DIAGNOSTIC = "DIAGNOSTIC"
    REQUIREMENT = "REQUIREMENT"


class CandidateStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    REJECTED = "REJECTED"
    CONFIRMED = "CONFIRMED"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    DEPRECATED = "DEPRECATED"


class CandidateSourceType(str, Enum):
    EXTERNAL_SOURCE = "EXTERNAL_SOURCE"
    BUSINESS = "BUSINESS"


class BusinessSourceType(str, Enum):
    STORAGE = "STORAGE"
    MAJOR_ISSUE = "MAJOR_ISSUE"
    HISTORICAL_CASE = "HISTORICAL_CASE"
    HARDWARE_CASE = "HARDWARE_CASE"
    OTHER = "OTHER"


class EvidenceLocation(StrictModel):
    """A model-provided locator only; source text is never accepted here."""

    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    page: int = Field(ge=1)
    section: str | None = None
    source_anchor: str | None = None


class KnowledgeCandidate(StrictModel):
    candidate_id: str = Field(min_length=1)
    candidate_source_type: CandidateSourceType = CandidateSourceType.EXTERNAL_SOURCE
    business_source_type: BusinessSourceType | None = None
    business_source_id: str | None = None
    business_source_version: str | None = None
    object_type: KnowledgeObjectType
    title: str = Field(min_length=1)
    summary: str | None = None
    content: str = Field(min_length=1)
    device_type: str | None = None
    scope: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    extraction_version: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: CandidateStatus = CandidateStatus.CANDIDATE
    producer: str = Field(default="KNOWLEDGE_EXTRACTION", min_length=1)
    contract_version: str = Field(
        default=KNOWLEDGE_CANDIDATE_CONTRACT_VERSION,
        pattern=r"^knowledge-candidate/v1$",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_candidate_source(self) -> "KnowledgeCandidate":
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("duplicate evidence_refs")
        if len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError("duplicate source_refs")

        if self.candidate_source_type == CandidateSourceType.EXTERNAL_SOURCE:
            if any(
                value is not None
                for value in (
                    self.business_source_type,
                    self.business_source_id,
                    self.business_source_version,
                )
            ):
                raise ValueError("external candidate cannot carry business provenance")
            if not self.extraction_version:
                raise ValueError("external candidate requires extraction_version")
            if not self.evidence_refs:
                raise ValueError("external candidate requires evidence_refs")
            return self

        if self.business_source_type is None:
            raise ValueError("business candidate requires business_source_type")
        if not self.business_source_id:
            raise ValueError("business candidate requires business_source_id")
        return self


class BusinessCandidateInput(StrictModel):
    candidate_id: str = Field(min_length=1)
    candidate_source_type: Literal["BUSINESS"]
    business_source_type: BusinessSourceType
    business_source_id: str = Field(min_length=1)
    business_source_version: str | None = None
    object_type: KnowledgeObjectType
    title: str = Field(min_length=1)
    summary: str | None = None
    content: str = Field(min_length=1)
    device_type: str | None = None
    scope: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_refs: list[str]
    evidence_refs: list[str]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    created_at: datetime
    producer: str = Field(min_length=1)
    contract_version: str = Field(pattern=r"^knowledge-candidate/v1$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeExtractionCandidateDraft(StrictModel):
    object_type: KnowledgeObjectType
    title: str = Field(min_length=1)
    summary: str | None = None
    content: str = Field(min_length=1)
    device_type: str | None = None
    scope: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    evidence_locations: list[EvidenceLocation] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class KnowledgeExtractionOutput(StrictModel):
    candidates: list[KnowledgeExtractionCandidateDraft] = Field(default_factory=list)
    unknowns_or_gaps: list[str] = Field(default_factory=list)


class EvidenceValidationStatus(str, Enum):
    VALID = "VALID"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"


class DuplicateStatus(str, Enum):
    NEW = "NEW"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    DUPLICATE = "DUPLICATE"


class ConflictStatus(str, Enum):
    NONE = "NONE"
    CONFLICT = "CONFLICT"


class KnowledgeEvaluation(StrictModel):
    evaluation_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    evidence_status: EvidenceValidationStatus
    source_valid: bool
    contract_valid: bool
    scope_valid: bool
    candidate_complete: bool
    duplicate_status: DuplicateStatus
    duplicate_object_ids: list[str] = Field(default_factory=list)
    conflict_status: ConflictStatus
    conflict_object_ids: list[str] = Field(default_factory=list)
    publish_readiness: bool
    review_ready: bool
    reasons: list[str] = Field(default_factory=list)
    evaluated_against_object_ids: list[str] = Field(default_factory=list)
    evaluation_version: str = "kp-d02-v1"


KNOWLEDGE_OBJECT_CONTRACT_VERSION = "knowledge-object/v1"


class ReviewAction(str, Enum):
    CONFIRM = "CONFIRM"
    EDIT = "EDIT"
    REJECT = "REJECT"


class ReviewStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class KnowledgeObjectStatus(str, Enum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    DEPRECATED = "DEPRECATED"


class ReviewRecord(StrictModel):
    review_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    action: ReviewAction
    review_status: ReviewStatus
    reviewed_by: str = Field(min_length=1)
    reviewed_at: datetime
    review_note: str | None = None
    input_evaluation_id: str = Field(min_length=1)
    effective_evaluation_id: str | None = None
    effective_snapshot_ref: str | None = None
    edit_fields: list[str] = Field(default_factory=list)


class KnowledgeObject(StrictModel):
    object_id: str = Field(min_length=1)
    object_version: int = Field(ge=1)
    contract_version: str = Field(
        default=KNOWLEDGE_OBJECT_CONTRACT_VERSION,
        pattern=r"^knowledge-object/v1$",
    )
    status: KnowledgeObjectStatus = KnowledgeObjectStatus.ACTIVE
    candidate_id: str = Field(min_length=1)
    review_id: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    candidate_source_type: CandidateSourceType
    business_source_type: BusinessSourceType | None = None
    business_source_id: str | None = None
    business_source_version: str | None = None
    object_type: KnowledgeObjectType
    title: str = Field(min_length=1)
    summary: str | None = None
    content: str = Field(min_length=1)
    device_type: str | None = None
    scope: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    producer: str = Field(min_length=1)
    published_by: str = Field(min_length=1)
    published_at: datetime
