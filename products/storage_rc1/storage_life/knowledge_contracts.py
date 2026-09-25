"""Version 1 response contracts for knowledge service callers."""
from typing import Literal

from pydantic import BaseModel


class SourceCreated(BaseModel):
    source_id: str
    verify_status: Literal["indexed", "verified", "rejected"]
    passage_count: int
    page_count: int
    reused: bool = False


class UrlSourceRequest(BaseModel):
    official_url: str
    title: str
    publisher: str
    version: str = ""


class SourceSummary(BaseModel):
    id: str
    title: str
    publisher: str
    official_url: str
    version: str
    source_type: str
    filename: str
    sha256: str
    page_count: int
    verify_status: Literal["indexed", "verified", "rejected"]
    verified_by: str | None
    verified_at: str | None
    created_at: str


class PassageSummary(BaseModel):
    id: int
    page_number: int
    section: str
    text: str
    extraction_method: str


class SourceDetail(SourceSummary):
    passages: list[PassageSummary]


class SourceReviewed(BaseModel):
    source_id: str
    verify_status: Literal["verified", "rejected"]


class Evidence(BaseModel):
    source_id: str
    passage_id: int | None = None
    title: str
    publisher: str
    page: int
    section: str
    original_text: str
    official_url: str
    local_url: str
    sha256: str | None = None
    verify_status: Literal["confirmed", "verified"]


class SearchItem(BaseModel):
    kind: Literal["confirmed_specification", "verified_knowledge"]
    fact: str
    evidence: Evidence


class SearchResponse(BaseModel):
    status: Literal["evidenced", "no_evidence"]
    items: list[SearchItem]
    answer: str


class PassageDetail(BaseModel):
    id: int
    source_id: str
    page_number: int
    section: str
    text: str
    extraction_method: str
    title: str
    publisher: str
    official_url: str
    version: str
    filename: str
    sha256: str
    verify_status: Literal["indexed", "verified", "rejected"]
