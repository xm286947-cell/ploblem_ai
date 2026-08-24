from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from .common import Metadata, VersionedDTO


class FilterMode(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"
    PREFER = "PREFER"
    EXPANDABLE = "EXPANDABLE"


class RetrievalField(VersionedDTO):
    field: str = Field(min_length=1)
    values: list[str | int | float | bool] = Field(default_factory=list)
    source: str = "EMPTY"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    completeness: float = Field(default=0.0, ge=0.0, le=1.0)
    base_weight: float = Field(default=0.0, ge=0.0)
    effective_weight: float = Field(default=0.0, ge=0.0)
    filter_mode: FilterMode | None = None
    evidence_type: str = ""


class RetrievalProfile(VersionedDTO):
    query_id: str = Field(min_length=1)
    product_profile: str = ""
    filters: list[RetrievalField] = Field(default_factory=list)
    high_weight: list[RetrievalField] = Field(default_factory=list)
    medium_weight: list[RetrievalField] = Field(default_factory=list)
    low_weight: list[RetrievalField] = Field(default_factory=list)
    channels: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=Metadata)


class CandidateCase(VersionedDTO):
    query_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    rank: int = Field(default=0, ge=0)
    retrieval_score: float = 0.0
    retrieval_reasons: list[str] = Field(default_factory=list)
    knowledge_case_ref: str | None = None
    metadata: Metadata = Field(default_factory=Metadata)
