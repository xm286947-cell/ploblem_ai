from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
