from __future__ import annotations

from pydantic import Field

from .common import DecisionType, Metadata, VersionedDTO


class RepeatDecision(VersionedDTO):
    query_id: str = Field(min_length=1)
    decision: DecisionType
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    candidate_case_ids: list[str] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=Metadata)
