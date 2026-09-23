"""Strict, clean-database DTOs for the P0 V2 quality analysis contract.

These models deliberately describe only the V2 contract. They do not accept
historical ``result_json`` shapes or their legacy key names.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SourceType = Literal["SOURCE_DATA", "AI_STANDARDIZED", "AI_INFERRED", "HUMAN_CONFIRMED"]
Axis = Literal[
    "DOMAIN", "LIFECYCLE", "TRIGGER", "FAILURE_MECHANISM", "MRC",
    "ENGINEERING_CAPABILITY", "MANAGEMENT_CAPABILITY",
]
ControlStatus = Literal[
    "NOT_DEFINED", "DEFINED_NOT_EXECUTED", "EXECUTED_INSUFFICIENT",
    "EFFECT_NOT_VERIFIED", "EFFECTIVE", "UNKNOWN",
]
AnalysisStage = Literal["occurrence", "escape", "recurrence", "capability_gap"]
AnalysisStatus = Literal["COMPLETED", "PARTIAL_FAILED", "FAILED"]


class StrictV2Model(BaseModel):
    """Reject undeclared properties so runner output cannot drift silently."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceV2DTO(StrictV2Model):
    source_type: SourceType = "AI_INFERRED"
    source_ref: str = "analysis"
    field_path: str | None = None
    excerpt: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def cap_ai_inference_confidence(self) -> "EvidenceV2DTO":
        if self.source_type == "AI_INFERRED" and self.confidence > 0.60:
            self.confidence = 0.60
        return self


class EvidenceValueV2DTO(StrictV2Model):
    value: Any = ""
    source_type: SourceType = "AI_INFERRED"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceV2DTO] = Field(default_factory=list)

    @model_validator(mode="after")
    def cap_ai_inference_confidence(self) -> "EvidenceValueV2DTO":
        if self.source_type == "AI_INFERRED" and self.confidence > 0.60:
            self.confidence = 0.60
        return self


class ClassifiedTermDTO(StrictV2Model):
    axis: Axis
    code: str = Field(min_length=1)
    role: Literal["PRIMARY", "SECONDARY"] = "SECONDARY"
    source_type: SourceType = "AI_INFERRED"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceV2DTO] = Field(default_factory=list)
    rationale: str = ""

    @model_validator(mode="after")
    def cap_ai_inference_confidence(self) -> "ClassifiedTermDTO":
        if self.source_type == "AI_INFERRED" and self.confidence > 0.60:
            self.confidence = 0.60
        return self


class OpenQuestionV2DTO(StrictV2Model):
    question_key: str = Field(min_length=1)
    target_path: str = ""
    changes_decision: list[str] = Field(default_factory=list)
    question: str = Field(min_length=1)
    why_it_matters: str = ""
    priority: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    answer_type: Literal["TEXT", "BOOLEAN", "SINGLE_SELECT"] = "TEXT"
    suggested_options: list[str] = Field(default_factory=list)


class MrcV2DTO(StrictV2Model):
    side: Literal["OCCURRENCE", "ESCAPE"]
    primary: ClassifiedTermDTO | None = None
    secondary: list[ClassifiedTermDTO] = Field(default_factory=list)
    control_status: ControlStatus = "UNKNOWN"
    summary: str = ""

    @model_validator(mode="after")
    def require_mrc_axis(self) -> "MrcV2DTO":
        if self.primary is not None and self.primary.axis != "MRC":
            raise ValueError("primary.axis must be MRC")
        if any(item.axis != "MRC" for item in self.secondary):
            raise ValueError("secondary[].axis must be MRC")
        return self


class KnownIssueLeakageV2DTO(StrictV2Model):
    status: Literal["YES", "NO", "UNKNOWN"] = "UNKNOWN"
    leakage_modes: list[str] = Field(default_factory=list)
    source_type: SourceType = "AI_INFERRED"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceV2DTO] = Field(default_factory=list)

    @model_validator(mode="after")
    def cap_ai_inference_confidence(self) -> "KnownIssueLeakageV2DTO":
        if self.source_type == "AI_INFERRED" and self.confidence > 0.60:
            self.confidence = 0.60
        return self


class OccurrenceAnalysisV2DTO(StrictV2Model):
    engineering_root_cause: EvidenceValueV2DTO = Field(default_factory=EvidenceValueV2DTO)
    management_contributing_factors: list[EvidenceValueV2DTO] = Field(default_factory=list)
    introduced_stage: str = "UNKNOWN"
    domain_tags: list[ClassifiedTermDTO] = Field(default_factory=list)
    lifecycle_tags: list[ClassifiedTermDTO] = Field(default_factory=list)
    trigger_tags: list[ClassifiedTermDTO] = Field(default_factory=list)
    failure_mechanism_tags: list[ClassifiedTermDTO] = Field(default_factory=list)
    mrc: MrcV2DTO = Field(default_factory=lambda: MrcV2DTO(side="OCCURRENCE"))
    open_questions: list[OpenQuestionV2DTO] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceV2DTO] = Field(default_factory=list)


class EscapeAnalysisV2DTO(StrictV2Model):
    expected_detection_stage: str = "UNKNOWN"
    actual_detection_stage: str = "UNKNOWN"
    escape_mechanism: EvidenceValueV2DTO = Field(default_factory=EvidenceValueV2DTO)
    missing_or_failed_control: EvidenceValueV2DTO = Field(default_factory=EvidenceValueV2DTO)
    control_status: ControlStatus = "UNKNOWN"
    known_issue_leakage: KnownIssueLeakageV2DTO = Field(default_factory=KnownIssueLeakageV2DTO)
    mrc: MrcV2DTO = Field(default_factory=lambda: MrcV2DTO(side="ESCAPE"))
    open_questions: list[OpenQuestionV2DTO] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceV2DTO] = Field(default_factory=list)


class RecurrenceAnalysisV2DTO(StrictV2Model):
    recurrence_risk_level: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = "UNKNOWN"
    existing_control_coverage: EvidenceValueV2DTO = Field(default_factory=EvidenceValueV2DTO)
    residual_risk: EvidenceValueV2DTO = Field(default_factory=EvidenceValueV2DTO)
    potential_affected_products: list[str] = Field(default_factory=list)
    potential_affected_versions: list[str] = Field(default_factory=list)
    horizontal_action_needed: bool = False
    customer_impact: EvidenceValueV2DTO = Field(default_factory=EvidenceValueV2DTO)
    open_questions: list[OpenQuestionV2DTO] = Field(default_factory=list)


class CapabilityGapV2DTO(StrictV2Model):
    gap_id: str = ""
    capability_axis: Literal["QUALITY_ENGINEERING", "QUALITY_MANAGEMENT"]
    capability_code: str = Field(min_length=1)
    governance_scope: Literal["PRODUCT", "CROSS_PRODUCT", "COMPANY"] = "PRODUCT"
    related_occurrence_mrc_codes: list[str] = Field(default_factory=list)
    related_escape_mrc_codes: list[str] = Field(default_factory=list)
    control_status: ControlStatus = "UNKNOWN"
    gap_description: str = ""
    build_target: str = ""
    technical_measure: str = ""
    first_action: str = ""
    owner_role: str = ""
    implementation_stage: str = "UNKNOWN"
    prevention_effect: str = ""
    verification_metric: str = ""
    priority: Literal["P0", "P1", "P2"] = "P2"
    source_type: SourceType = "AI_INFERRED"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceV2DTO] = Field(default_factory=list)

    @model_validator(mode="after")
    def cap_ai_inference_confidence(self) -> "CapabilityGapV2DTO":
        if self.source_type == "AI_INFERRED" and self.confidence > 0.60:
            self.confidence = 0.60
        return self


class IssueAnalysisV2Envelope(StrictV2Model):
    contract_version: Literal["2.0.0"] = "2.0.0"
    analysis_set_id: str = Field(min_length=1)
    knowledge_id: str = Field(min_length=1)
    issue_version_id: str = Field(min_length=1)
    taxonomy_version_id: str = Field(min_length=1)
    classification_mapping_versions: dict[str, str]
    prompt_versions: dict[str, str]
    scoring_version_id: str | None = None
    analysis_profile: dict[str, Any]
    source_coverage: dict[str, Any]
    input_hash: str = Field(min_length=1)
    status: AnalysisStatus
    occurrence: OccurrenceAnalysisV2DTO | None = None
    escape: EscapeAnalysisV2DTO | None = None
    recurrence: RecurrenceAnalysisV2DTO | None = None
    capability_gaps: list[CapabilityGapV2DTO] = Field(default_factory=list)
    classification_consistency: str = "PENDING_CONFIRMATION"
    warnings: list[str] = Field(default_factory=list)
