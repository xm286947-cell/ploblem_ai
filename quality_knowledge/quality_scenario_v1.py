"""Quality Scenario V1 business contract.

This module is the single schema contract for QS-MVP-02.  It is intentionally
independent from the legacy ScenarioRepository and from Reverse Quality/Runtime.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "quality-scenario-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ScenarioStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"


class ScenarioTriggerSource(StrEnum):
    HIGH_PERCEPTION = "HIGH_PERCEPTION"
    RND_VALUE = "RND_VALUE"


class ScenarioActor(StrEnum):
    AI = "AI"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class ScenarioReviewStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class ScenarioProvenanceType(StrEnum):
    FACT = "FACT"
    INFERRED = "INFERRED"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"


class ScenarioRelationType(StrEnum):
    PRIMARY = "PRIMARY"
    SUPPORTING = "SUPPORTING"
    RELATED = "RELATED"


class ScenarioSourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str = Field(min_length=1, max_length=200)
    source_type: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=200)
    canonical_itr: str = Field(default="", max_length=200)
    product_code: str = Field(default="", max_length=120)
    product_version: str = Field(default="", max_length=120)
    relation_type: ScenarioRelationType = ScenarioRelationType.PRIMARY


class ScenarioEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=240)
    source_ref: str = Field(min_length=1, max_length=200)
    evidence_type: str = Field(min_length=1, max_length=80)
    source_text: str = Field(default="", max_length=4000)
    content_ref: str = Field(default="", max_length=1000)
    supports: list[str] = Field(default_factory=list, min_length=1, max_length=32)
    source_type: ScenarioProvenanceType
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_content_pointer(self):
        if not self.source_text.strip() and not self.content_ref.strip():
            raise ValueError("EVIDENCE_SOURCE_TEXT_OR_CONTENT_REF_REQUIRED")
        return self


class ScenarioMissingInformation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(default="", max_length=120)
    reason: str = Field(default="", max_length=500)
    question: str = Field(min_length=1, max_length=500)
    evidence_needed: list[str] = Field(default_factory=list, max_length=16)
    status: Literal["PENDING", "CONFIRMED", "NOT_APPLICABLE"] = "PENDING"
    answer: str = Field(default="", max_length=2000)


class ScenarioReviewMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: ScenarioReviewStatus = ScenarioReviewStatus.PENDING
    reviewer: str = Field(default="", max_length=120)
    reviewed_at: str = Field(default="", max_length=64)
    comment: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def require_reviewer_for_decision(self):
        if self.review_status != ScenarioReviewStatus.PENDING:
            if not self.reviewer.strip() or not self.reviewed_at.strip():
                raise ValueError("SCENARIO_REVIEWER_AND_TIME_REQUIRED")
        return self


class ScenarioConfirmationMetadata(BaseModel):
    """Lightweight dual-role confirmation facts, not an approval workflow."""

    model_config = ConfigDict(extra="forbid")

    quality_confirmed_by: str = Field(default="", max_length=120)
    quality_confirmed_at: str = Field(default="", max_length=64)
    technical_confirmed_by: str = Field(default="", max_length=120)
    technical_confirmed_at: str = Field(default="", max_length=64)
    confirmation_note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_actor_time_pairs(self):
        if bool(self.quality_confirmed_by.strip()) != bool(self.quality_confirmed_at.strip()):
            raise ValueError("SCENARIO_QUALITY_CONFIRMATION_ACTOR_TIME_PAIR_REQUIRED")
        if bool(self.technical_confirmed_by.strip()) != bool(self.technical_confirmed_at.strip()):
            raise ValueError("SCENARIO_TECHNICAL_CONFIRMATION_ACTOR_TIME_PAIR_REQUIRED")
        return self

    @property
    def quality_confirmed(self) -> bool:
        return bool(self.quality_confirmed_by.strip() and self.quality_confirmed_at.strip())

    @property
    def technical_confirmed(self) -> bool:
        return bool(self.technical_confirmed_by.strip() and self.technical_confirmed_at.strip())


class ScenarioVersionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_by: str = Field(default="", max_length=120)
    created_at: str = Field(default_factory=utc_now, max_length=64)
    updated_at: str = Field(default_factory=utc_now, max_length=64)
    published_at: str = Field(default="", max_length=64)
    parent_scenario_version: int | None = Field(default=None, ge=1)
    change_summary: str = Field(default="", max_length=1000)


class QualityScenarioFieldsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_code: str = Field(min_length=1, max_length=120)
    product_name: str = Field(default="", max_length=200)
    lifecycle_stage_code: str = Field(default="", max_length=120)
    lifecycle_stage_name: str = Field(default="", max_length=200)
    business_activity_code: str = Field(default="", max_length=120)
    business_activity_name: str = Field(default="", max_length=200)
    business_goal: str = Field(default="", max_length=1000)

    scenario_name: str = Field(min_length=1, max_length=240)
    scenario_description: str = Field(default="", max_length=4000)
    quality_concern_code: str = Field(default="", max_length=120)
    quality_concern_name: str = Field(default="", max_length=400)
    trigger_source: ScenarioTriggerSource | None = None
    trigger_reason: str = Field(default="", max_length=1000)
    trigger_condition: str = Field(default="", max_length=2000)
    expected_result: str = Field(default="", max_length=2000)
    applicability_scope: str = Field(default="", max_length=2000)


def _validate_reference_integrity(
    source_problem_refs: list[ScenarioSourceReference],
    evidence_refs: list[ScenarioEvidenceReference],
) -> None:
    source_refs = [item.source_ref for item in source_problem_refs]
    if len(source_refs) != len(set(source_refs)):
        raise ValueError("SCENARIO_SOURCE_REF_DUPLICATED")
    evidence_ids = [item.evidence_id for item in evidence_refs]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("SCENARIO_EVIDENCE_ID_DUPLICATED")
    valid_sources = set(source_refs)
    if any(item.source_ref not in valid_sources for item in evidence_refs):
        raise ValueError("SCENARIO_EVIDENCE_SOURCE_REF_NOT_FOUND")


def _pending_missing(items: list[ScenarioMissingInformation]) -> bool:
    return any(item.status == "PENDING" for item in items)


class ScenarioCandidateV1(QualityScenarioFieldsV1):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["quality-scenario-v1"] = SCHEMA_VERSION
    candidate_id: str = Field(min_length=1, max_length=200)
    status: Literal[ScenarioStatus.CANDIDATE] = ScenarioStatus.CANDIDATE

    source_problem_refs: list[ScenarioSourceReference] = Field(default_factory=list)
    evidence_refs: list[ScenarioEvidenceReference] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    missing_information: list[ScenarioMissingInformation] = Field(default_factory=list)

    source_result_version: str = Field(default="", max_length=120)
    source_analysis_id: str = Field(default="", max_length=200)
    source_run_id: str = Field(default="", max_length=200)
    source_run_seq: int = Field(default=0, ge=0)
    producer: str = Field(default="reverse-quality-v0.1", max_length=120)
    field_evidence: dict[str, dict] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now, max_length=64)

    @model_validator(mode="after")
    def validate_candidate_refs(self):
        _validate_reference_integrity(self.source_problem_refs, self.evidence_refs)
        return self


class QualityScenarioV1(QualityScenarioFieldsV1):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, max_length=200)
    schema_version: Literal["quality-scenario-v1"] = SCHEMA_VERSION
    scenario_version: int = Field(default=1, ge=1)
    status: ScenarioStatus = ScenarioStatus.CANDIDATE

    source_problem_refs: list[ScenarioSourceReference] = Field(default_factory=list)
    evidence_refs: list[ScenarioEvidenceReference] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    missing_information: list[ScenarioMissingInformation] = Field(default_factory=list)

    review: ScenarioReviewMetadata = Field(default_factory=ScenarioReviewMetadata)
    confirmation: ScenarioConfirmationMetadata = Field(default_factory=ScenarioConfirmationMetadata)
    version: ScenarioVersionMetadata = Field(default_factory=ScenarioVersionMetadata)

    @model_validator(mode="after")
    def validate_contract(self):
        _validate_reference_integrity(self.source_problem_refs, self.evidence_refs)
        if self.status in {ScenarioStatus.CONFIRMED, ScenarioStatus.PUBLISHED}:
            self.assert_formal_ready()
        if self.status == ScenarioStatus.PUBLISHED and not self.version.published_at.strip():
            raise ValueError("SCENARIO_PUBLISHED_AT_REQUIRED")
        return self

    def assert_formal_ready(self) -> None:
        required = {
            "lifecycle_stage_code": self.lifecycle_stage_code,
            "business_activity_code": self.business_activity_code,
            "scenario_name": self.scenario_name,
            "expected_result": self.expected_result,
        }
        missing = [key for key, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError("SCENARIO_FORMAL_FIELD_REQUIRED:" + ",".join(missing))
        if not self.quality_concern_code.strip() and not self.quality_concern_name.strip():
            raise ValueError("SCENARIO_QUALITY_CONCERN_REQUIRED")
        if self.trigger_source is None:
            raise ValueError("SCENARIO_TRIGGER_SOURCE_REQUIRED")
        if not self.trigger_reason.strip():
            raise ValueError("SCENARIO_TRIGGER_REASON_REQUIRED")
        if not self.source_problem_refs:
            raise ValueError("SCENARIO_SOURCE_PROBLEM_REQUIRED")
        if not self.evidence_refs:
            raise ValueError("SCENARIO_EVIDENCE_REQUIRED")
        if self.blockers:
            raise ValueError("SCENARIO_BLOCKERS_NOT_RESOLVED")
        if _pending_missing(self.missing_information):
            raise ValueError("SCENARIO_MISSING_INFORMATION_PENDING")
        if self.review.review_status != ScenarioReviewStatus.CONFIRMED:
            raise ValueError("SCENARIO_REVIEW_NOT_CONFIRMED")
        if not self.confirmation.quality_confirmed:
            raise ValueError("SCENARIO_QUALITY_CONFIRMATION_REQUIRED")
        if not self.confirmation.technical_confirmed:
            raise ValueError("SCENARIO_TECHNICAL_CONFIRMATION_REQUIRED")


ALLOWED_STATUS_TRANSITIONS: dict[ScenarioStatus, set[ScenarioStatus]] = {
    ScenarioStatus.CANDIDATE: {ScenarioStatus.CONFIRMED, ScenarioStatus.REJECTED},
    ScenarioStatus.CONFIRMED: {ScenarioStatus.PUBLISHED},
    ScenarioStatus.PUBLISHED: set(),
    ScenarioStatus.REJECTED: set(),
}


def validate_status_transition(
    current: ScenarioStatus | str,
    target: ScenarioStatus | str,
    *,
    actor: ScenarioActor | str,
    blockers: list[str] | tuple[str, ...] = (),
    missing_information: list[ScenarioMissingInformation] | None = None,
    has_source_problem: bool = True,
    has_evidence: bool = True,
) -> ScenarioStatus:
    current_status = ScenarioStatus(current)
    target_status = ScenarioStatus(target)
    actor_type = ScenarioActor(actor)
    if target_status not in ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise ValueError(f"SCENARIO_STATUS_TRANSITION_INVALID:{current_status}->{target_status}")
    if actor_type == ScenarioActor.AI:
        raise ValueError("SCENARIO_STATUS_AI_DECISION_FORBIDDEN")
    if target_status in {ScenarioStatus.CONFIRMED, ScenarioStatus.PUBLISHED}:
        if blockers:
            raise ValueError("SCENARIO_BLOCKERS_NOT_RESOLVED")
        if _pending_missing(missing_information or []):
            raise ValueError("SCENARIO_MISSING_INFORMATION_PENDING")
        if not has_source_problem:
            raise ValueError("SCENARIO_SOURCE_PROBLEM_REQUIRED")
        if not has_evidence:
            raise ValueError("SCENARIO_EVIDENCE_REQUIRED")
    return target_status


def scenario_from_candidate(
    candidate: ScenarioCandidateV1,
    scenario_id: str,
    *,
    created_by: str = "",
) -> QualityScenarioV1:
    payload = candidate.model_dump(
        mode="json",
        exclude={
            "candidate_id", "source_result_version", "source_analysis_id",
            "source_run_id", "source_run_seq", "producer", "field_evidence",
            "created_at",
        },
    )
    payload.pop("schema_version", None)
    payload.pop("status", None)
    return QualityScenarioV1(
        scenario_id=scenario_id,
        schema_version=SCHEMA_VERSION,
        scenario_version=1,
        status=ScenarioStatus.CANDIDATE,
        review=ScenarioReviewMetadata(),
        version=ScenarioVersionMetadata(created_by=created_by or candidate.producer),
        **payload,
    )


__all__ = [
    "SCHEMA_VERSION",
    "ScenarioStatus",
    "ScenarioTriggerSource",
    "ScenarioActor",
    "ScenarioReviewStatus",
    "ScenarioProvenanceType",
    "ScenarioRelationType",
    "ScenarioSourceReference",
    "ScenarioEvidenceReference",
    "ScenarioMissingInformation",
    "ScenarioReviewMetadata",
    "ScenarioConfirmationMetadata",
    "ScenarioVersionMetadata",
    "QualityScenarioFieldsV1",
    "ScenarioCandidateV1",
    "QualityScenarioV1",
    "ALLOWED_STATUS_TRANSITIONS",
    "validate_status_transition",
    "scenario_from_candidate",
]
