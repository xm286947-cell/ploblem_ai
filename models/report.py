from __future__ import annotations

from pydantic import Field

from .common import Metadata, VersionedDTO
from .decision import RepeatDecision
from .recommendation import ReviewRecommendation
from .similarity import SimilarityResult


class RepeatReport(VersionedDTO):
    report_id: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    decision: RepeatDecision
    similarities: list[SimilarityResult] = Field(default_factory=list)
    recommendation: ReviewRecommendation | None = None
    metadata: Metadata = Field(default_factory=Metadata)
