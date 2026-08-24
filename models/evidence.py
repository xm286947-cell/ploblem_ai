from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import Field, field_validator

from .common import EvidenceReference, EvidenceType, StrictDTO

T = TypeVar("T")


class EvidenceField(StrictDTO, Generic[T]):
    value: T
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_type: EvidenceType = EvidenceType.UNKNOWN
    reason: str = ""
    source_refs: list[EvidenceReference] = Field(default_factory=list)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        return value.strip()
