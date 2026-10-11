"""Additive, opt-in normalization for Quality Scenario *formal* source records.

This module is not imported by the legacy workbench or production routes.
It performs no I/O and never edits historical source data or scenario tables.

Only THOROUGH_SOLUTION_ORDER and MISSED_TEST_ANALYSIS can produce scenarios.
ITR is optional problem/trace context, NOT a production source.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

CONTRACT_VERSION = "qs-formal-source/v1"


class FormalSourceType(str, Enum):
    THOROUGH_SOLUTION_ORDER = "THOROUGH_SOLUTION_ORDER"
    MISSED_TEST_ANALYSIS = "MISSED_TEST_ANALYSIS"


class EvidenceKind(str, Enum):
    FACT = "FACT"
    INFERRED = "INFERRED"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"


class FormalSourceError(ValueError):
    """Controlled refusal: source identity, relationship or evidence is unsafe."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _identifier(value: Any, code: str) -> str:
    """Do not invent business IDs; reject empty or control-character IDs."""
    if not isinstance(value, str):
        raise FormalSourceError(code)
    text = value.strip()
    if not text or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise FormalSourceError(code)
    return text


def _optional_identifier(value: Any, code: str) -> str:
    return "" if value is None or value == "" else _identifier(value, code)


def _items(value: Any, code: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise FormalSourceError(code)
    return value


@dataclass(frozen=True)
class FormalEvidence:
    evidence_id: str
    source_ref: str
    locator: str
    excerpt: str
    supports: tuple[str, ...]
    evidence_kind: EvidenceKind
    confidence: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_ref": self.source_ref,
            "locator": self.locator,
            "excerpt": self.excerpt,
            "supports": list(self.supports),
            "evidence_kind": self.evidence_kind.value,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class FormalSource:
    source_type: FormalSourceType
    source_record_id: str
    source_ref: str
    problem_ref: str
    facts: Mapping[str, Any]
    evidence: tuple[FormalEvidence, ...]
    fields_without_evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type.value,
            "source_record_id": self.source_record_id,
            "source_ref": self.source_ref,
            "problem_ref": self.problem_ref,
            "facts": dict(self.facts),
            "evidence": [entry.as_dict() for entry in self.evidence],
            "fields_without_evidence": list(self.fields_without_evidence),
        }


def _parse_evidence(
    value: Any, *, source_ref: str, facts: Mapping[str, Any]
) -> tuple[FormalEvidence, ...]:
    entries: list[FormalEvidence] = []
    seen: set[str] = set()
    for row in _items(value, "INVALID_EVIDENCE_LIST"):
        if not isinstance(row, Mapping):
            raise FormalSourceError("INVALID_EVIDENCE")
        eid = _identifier(row.get("evidence_id"), "EVIDENCE_ID_REQUIRED")
        if eid in seen:
            raise FormalSourceError("DUPLICATE_EVIDENCE_ID")
        seen.add(eid)
        if row.get("source_ref") not in (None, "", source_ref):
            raise FormalSourceError("EVIDENCE_SOURCE_REF_MISMATCH")
        locator = _optional_identifier(row.get("locator"), "INVALID_EVIDENCE_LOCATOR")
        excerpt = _optional_identifier(row.get("excerpt"), "INVALID_EVIDENCE_EXCERPT")
        if not locator and not excerpt:
            raise FormalSourceError("EVIDENCE_CONTENT_REQUIRED")
        names = tuple(_identifier(field, "INVALID_SUPPORTED_FIELD")
                      for field in _items(row.get("supports"), "EVIDENCE_SUPPORTS_REQUIRED"))
        if not names or len(set(names)) != len(names):
            raise FormalSourceError("INVALID_EVIDENCE_SUPPORTS")
        if any(name not in facts for name in names):
            raise FormalSourceError("EVIDENCE_SUPPORTS_UNKNOWN_FACT")
        try:
            evidence_kind = EvidenceKind(row.get("evidence_kind"))
        except (ValueError, TypeError):
            raise FormalSourceError("INVALID_EVIDENCE_KIND") from None
        confidence = row.get("confidence")
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (float, int)):
                raise FormalSourceError("INVALID_EVIDENCE_CONFIDENCE")
            confidence = float(confidence)
            if not 0 <= confidence <= 1:
                raise FormalSourceError("INVALID_EVIDENCE_CONFIDENCE")
        entries.append(FormalEvidence(eid, source_ref, locator, excerpt, names, evidence_kind, confidence))
    return tuple(entries)


def normalize_formal_sources(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate an explicitly related source bundle without generating any facts.

    For bundles with >1 record a nonempty *matching* problem_ref is mandatory:
    never infer relationships from similar titles, customer or an ITR substring.
    Single-source input is permitted and marked PARTIAL.
    'FULL' denotes presence of both official source *types*, not complete facts.
    No generated candidate may be confirmed or published by this function.
    """
    rows = _items(records, "INVALID_SOURCE_LIST")
    if not rows:
        raise FormalSourceError("FORMAL_SOURCE_REQUIRED")
    parsed: list[FormalSource] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise FormalSourceError("INVALID_SOURCE_RECORD")
        try:
            source_type = FormalSourceType(row.get("source_type"))
        except (ValueError, TypeError):
            raise FormalSourceError("UNSUPPORTED_FORMAL_SOURCE_TYPE") from None
        record_id = _identifier(row.get("source_record_id"), "SOURCE_RECORD_ID_REQUIRED")
        source_ref = f"{source_type.value}:{record_id}"
        if source_ref in seen:
            raise FormalSourceError("DUPLICATE_FORMAL_SOURCE")
        seen.add(source_ref)
        problem_ref = _optional_identifier(row.get("problem_ref"), "INVALID_PROBLEM_REF")
        raw_facts = row.get("facts", {})
        if not isinstance(raw_facts, Mapping):
            raise FormalSourceError("INVALID_SOURCE_FACTS")
        facts: dict[str, Any] = {}
        for key, value in raw_facts.items():
            name = _identifier(key, "INVALID_FACT_NAME")
            if name in facts:
                raise FormalSourceError("DUPLICATE_FACT_NAME")
            # Preserve absence as absence. Never synthesize missing facts.
            facts[name] = value
        evidence = _parse_evidence(row.get("evidence", []), source_ref=source_ref, facts=facts)
        evidenced = {name for item in evidence for name in item.supports
                     if item.evidence_kind in (EvidenceKind.FACT, EvidenceKind.HUMAN_CONFIRMED)}
        missing = tuple(sorted(
            key for key, value in facts.items()
            if value not in (None, "") and key not in evidenced
        ))
        parsed.append(FormalSource(
            source_type, record_id, source_ref, problem_ref, facts, evidence, missing
        ))
    if len(parsed) > 1:
        refs = {item.problem_ref for item in parsed}
        if "" in refs or len(refs) != 1:
            raise FormalSourceError("SOURCE_RELATION_UNVERIFIED")
    source_types = {item.source_type for item in parsed}
    coverage = "FULL" if len(source_types) == len(FormalSourceType) else "PARTIAL"
    return {
        "contract_version": CONTRACT_VERSION,
        "production_source_types": [s.value for s in sorted(source_types, key=lambda v: v.value)],
        "source_coverage": coverage,
        "problem_ref_context": parsed[0].problem_ref,
        "sources": [item.as_dict() for item in parsed],
        "source_refs": [item.source_ref for item in parsed],
        "missing_evidence": [
            {"source_ref": item.source_ref, "field": field}
            for item in parsed for field in item.fields_without_evidence
        ],
        "publish_ready": False,
        "status": "SOURCE_CONTEXT_ONLY",
    }


__all__ = [
    "CONTRACT_VERSION", "FormalSourceError", "FormalSourceType", "EvidenceKind",
    "FormalSource", "FormalEvidence", "normalize_formal_sources",
]
