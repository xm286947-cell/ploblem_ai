from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

DTO_VERSION = "1.0.0"


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=True)


class VersionedDTO(StrictDTO):
    dto_version: str = Field(default=DTO_VERSION, min_length=1)


class EvidenceType(str, Enum):
    EXPLICIT = "EXPLICIT"
    SUMMARIZED = "SUMMARIZED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class DecisionType(str, Enum):
    REPEAT = "REPEAT"
    POSSIBLE_REPEAT = "POSSIBLE_REPEAT"
    NOT_REPEAT = "NOT_REPEAT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class SimilarityLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class EvidenceReference(StrictDTO):
    source_type: str = Field(default="FIELD", min_length=1)
    source_id: str = Field(min_length=1)
    field_path: str | None = None
    excerpt: str | None = None


class WarningItem(StrictDTO):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: str | None = None


class Metadata(StrictDTO):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    producer: str | None = None
    producer_version: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_datetime(cls, value: Any) -> Any:
        if isinstance(value, str) and value.endswith("Z"):
            return value[:-1] + "+00:00"
        return value
