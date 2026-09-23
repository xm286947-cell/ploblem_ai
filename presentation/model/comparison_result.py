from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ComparisonResult:
    reason_compare: list[dict[str, Any]] = field(default_factory=list)
    root_cause_compare: dict[str, Any] = field(default_factory=dict)
    solution_compare: dict[str, Any] = field(default_factory=dict)
    recommendation_reasons: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
