from __future__ import annotations

from pydantic import Field

from .common import Metadata, VersionedDTO


class ReviewRecommendation(VersionedDTO):
    query_id: str = Field(min_length=1)
    summary: str = ""
    actions: list[str] = Field(default_factory=list)
    reference_case_ids: list[str] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=Metadata)
