"""The single cross-domain Common Evidence Contract.

Domain models remain owned by their producer.  This module is deliberately a
small value contract plus pure adapters; it has no repository, database, or
producer-model imports.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


COMMON_EVIDENCE_CONTRACT_VERSION = "common-evidence/v1.0"
COMMON_EVIDENCE_CONTRACT_ID = "COMMON_EVIDENCE_CONTRACT_V1.0"


class CommonEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommonEvidenceSource(CommonEvidenceModel):
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)


class CommonEvidenceLocator(CommonEvidenceModel):
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    anchor: str | None = None


class CommonEvidence(CommonEvidenceModel):
    evidence_id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    source: CommonEvidenceSource
    locator: CommonEvidenceLocator = Field(default_factory=CommonEvidenceLocator)
    excerpt: str | None = None
    source_text: str | None = None
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_ref: str | None = None
    source_reference: str | None = None
    producer_domain: str = Field(min_length=1)
    producer_object_id: str = Field(min_length=1)
    producer_object_version: str = Field(min_length=1)
    verification_status: str = Field(min_length=1)
    evidence_status: str = Field(min_length=1)
    created_at: datetime
    contract_version: str = Field(
        default=COMMON_EVIDENCE_CONTRACT_VERSION,
        pattern=r"^common-evidence/v1\.0$",
    )

    @model_validator(mode="after")
    def validate_source_reference(self) -> "CommonEvidence":
        if not (self.source_ref or self.source_reference):
            raise ValueError("source_ref or source_reference is required")
        return self


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value and value[key] not in (None, ""):
            return value[key]
    return None


def _source(value: Mapping[str, Any], *, domain: str) -> CommonEvidenceSource:
    source = value.get("source") if isinstance(value.get("source"), Mapping) else value
    source_id = _text(_first(source, "source_id", "source_document_id", "sourceId"))
    source_type = _text(_first(source, "source_type", "sourceType", "document_type")) or domain
    source_version = _text(_first(source, "source_version", "source_revision", "revision", "version")) or "UNVERSIONED"
    if not source_id:
        source_id = _text(_first(value, "source_ref", "source_reference"))
    return CommonEvidenceSource(
        source_type=source_type,
        source_id=source_id,
        source_version=source_version,
    )


def _locator(value: Mapping[str, Any]) -> CommonEvidenceLocator:
    raw = value.get("locator") if isinstance(value.get("locator"), Mapping) else value
    nested = raw.get("value") if isinstance(raw.get("value"), Mapping) else raw
    page = nested.get("page")
    return CommonEvidenceLocator(
        page=page,
        section=_first(nested, "section", "section_path"),
        anchor=_first(nested, "anchor", "source_anchor", "paragraph", "block_id", "passage_id"),
    )


def to_common_evidence(
    value: CommonEvidence | Mapping[str, Any],
    *,
    domain: str,
    producer_object_id: str | None = None,
    producer_object_version: str | int | None = None,
) -> CommonEvidence:
    """Map one producer-owned evidence shape without coupling to its model."""
    if isinstance(value, CommonEvidence):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("evidence must be a mapping")
    source_payload = value.get("source") if isinstance(value.get("source"), Mapping) else {}
    source_ref = _text(
        _first(value, "source_ref", "source_reference", "uri", "source_ref_uri", "official_url", "local_url")
        or _first(source_payload, "uri", "source_ref", "source_reference")
    ) or None
    excerpt = _first(value, "excerpt", "excerpt_or_caption", "original_text")
    source_text = _first(value, "source_text", "raw_text", "text")
    producer_id = _text(producer_object_id or _first(value, "producer_object_id", "object_id", "case_id", "candidate_id", "source_id"))
    if not producer_id:
        producer_id = _text(source_ref)
    created = _first(value, "created_at", "published_at", "verified_at") or datetime.now(timezone.utc)
    return CommonEvidence(
        evidence_id=_text(value.get("evidence_id")),
        evidence_type=_text(_first(value, "evidence_type", "type", "source_type")) or "UNKNOWN",
        source=_source(value, domain=domain),
        locator=_locator(value),
        excerpt=None if excerpt is None else str(excerpt),
        source_text=None if source_text is None else str(source_text),
        content_hash=_first(value, "content_hash", "sha256"),
        source_ref=source_ref,
        source_reference=source_ref,
        producer_domain=domain,
        producer_object_id=producer_id,
        producer_object_version=_text(producer_object_version or _first(value, "producer_object_version", "object_version", "revision", "source_revision")) or "1",
        verification_status=_text(_first(value, "verification_status", "verify_status", "verification", "status")) or "UNVERIFIED",
        evidence_status=_text(_first(value, "evidence_status", "source_status", "verify_status", "status")) or "AVAILABLE",
        created_at=created,
    )


def map_external_source_evidence(value: Mapping[str, Any], **kwargs: Any) -> CommonEvidence:
    return to_common_evidence(value, domain="EXTERNAL_SOURCE", **kwargs)


def map_business_object_evidence(value: Mapping[str, Any], **kwargs: Any) -> CommonEvidence:
    return to_common_evidence(value, domain="BUSINESS_OBJECT", **kwargs)


def map_historical_case_evidence(value: Mapping[str, Any], **kwargs: Any) -> CommonEvidence:
    return to_common_evidence(value, domain="HISTORICAL_CASE", **kwargs)


def map_major_issue_evidence(value: Mapping[str, Any], **kwargs: Any) -> CommonEvidence:
    return to_common_evidence(value, domain="MAJOR_ISSUE", **kwargs)


def map_hardware_case_evidence(value: Mapping[str, Any], **kwargs: Any) -> CommonEvidence:
    return to_common_evidence(value, domain="HARDWARE_CASE", **kwargs)


def map_quality_scenario_evidence(value: Mapping[str, Any], **kwargs: Any) -> CommonEvidence:
    return to_common_evidence(value, domain="QUALITY_SCENARIO", **kwargs)


__all__ = [
    "COMMON_EVIDENCE_CONTRACT_ID",
    "COMMON_EVIDENCE_CONTRACT_VERSION",
    "CommonEvidence",
    "CommonEvidenceLocator",
    "CommonEvidenceSource",
    "to_common_evidence",
    "map_external_source_evidence",
    "map_business_object_evidence",
    "map_historical_case_evidence",
    "map_major_issue_evidence",
    "map_hardware_case_evidence",
    "map_quality_scenario_evidence",
]
