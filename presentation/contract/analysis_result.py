from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class AnalysisResult:
    """Stable handoff object from the analysis pipeline to Presentation.

    The object carries the M8.4 result without changing analysis semantics.
    Presentation code may read it, but must not perform retrieval, similarity,
    recommendation, or decision work.
    """

    metadata: dict[str, Any] = field(default_factory=dict)
    final_decision: str = "INSUFFICIENT_EVIDENCE"
    overall_confidence: float = 0.0
    best_case: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    analysis_status: str = "SKIPPED"
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def query_id(self) -> str:
        return str(self.metadata.get("query_id") or "")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AnalysisResult":
        if not isinstance(value, Mapping):
            raise TypeError("AnalysisResult.from_mapping requires a mapping")
        return cls(
            metadata=dict(value.get("metadata") or {}),
            final_decision=str(value.get("final_decision") or "INSUFFICIENT_EVIDENCE"),
            overall_confidence=float(value.get("overall_confidence") or 0.0),
            best_case=dict(value.get("best_case") or {}),
            candidates=[dict(item) for item in (value.get("candidates") or []) if isinstance(item, Mapping)],
            analysis_status=str(value.get("analysis_status") or "SKIPPED"),
            warnings=[dict(item) for item in (value.get("warnings") or []) if isinstance(item, Mapping)],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
