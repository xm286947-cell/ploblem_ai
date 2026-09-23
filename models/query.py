from __future__ import annotations

from pydantic import Field

from .common import Metadata, VersionedDTO
from .evidence import EvidenceField


class QueryContext(VersionedDTO):
    query_id: str = Field(min_length=1)
    product: str = ""
    ipmt: str = ""
    spdt: str = ""
    responsible_department_level2: str = ""
    problem_description: str = ""
    source_file: str | None = None
    problem_summary: EvidenceField[str] | None = None
    standard_problem_description: EvidenceField[str] | None = None
    feature: EvidenceField[str] | None = None
    phenomena: list[EvidenceField[str]] = Field(default_factory=list)
    trigger_conditions: list[EvidenceField[str]] = Field(default_factory=list)
    failure_mechanisms: list[EvidenceField[str]] = Field(default_factory=list)
    possible_root_causes: list[EvidenceField[str]] = Field(default_factory=list)
    keywords: list[EvidenceField[str]] = Field(default_factory=list)
    tags: list[EvidenceField[str]] = Field(default_factory=list)
    overall_confidence: EvidenceField[float] | None = None
    metadata: Metadata = Field(default_factory=Metadata)
