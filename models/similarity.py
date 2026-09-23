from __future__ import annotations

from pydantic import Field

from .common import Metadata, SimilarityLevel, VersionedDTO


class SimilarityDimension(VersionedDTO):
    score: int = Field(ge=0, le=100)
    assessment: str
    query_evidence: list[str] = Field(default_factory=list)
    case_evidence: list[str] = Field(default_factory=list)
    reason: str = ""


class SimilarityResult(VersionedDTO):
    query_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    retrieval_rank: int = Field(default=0, ge=0)
    retrieval_score: float = 0.0
    dimensions: dict[str, SimilarityDimension] = Field(default_factory=dict)
    overall_score: int = Field(default=0, ge=0, le=100)
    overall_level: SimilarityLevel = SimilarityLevel.UNKNOWN
    key_similarities: list[str] = Field(default_factory=list)
    key_differences: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    analysis_summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: Metadata = Field(default_factory=Metadata)
