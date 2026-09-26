"""Domain adapters for the frozen Common Evidence Contract.

The adapter is deliberately transport-only: it normalizes producer-owned
Evidence into a stable projection and never opens a producer repository.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

COMMON_EVIDENCE_CONTRACT_VERSION = "common-evidence/v1.0"


class CommonEvidenceContractError(ValueError):
    """Raised when a producer payload cannot be mapped without guessing."""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _source_ref(source_id: str | None, source_version: str | None) -> str | None:
    if source_id and source_version:
        return f"{source_id}@{source_version}"
    return None


def map_common_evidence(
    raw: Mapping[str, Any],
    *,
    producer_domain: str,
    producer_object_id: str | None = None,
    producer_object_version: str | int | None = None,
) -> dict[str, Any]:
    """Map one domain-owned Evidence object into Common Evidence.

    Only explicit source/locator/provenance values are copied. A deterministic
    `SOURCE_EXCERPT` type is used for legacy evidence that contains an excerpt
    but no explicit evidence type; this classifies the shape and does not infer
    any source fact.
    """

    if not isinstance(raw, Mapping):
        raise CommonEvidenceContractError("evidence must be an object")
    domain = _text(producer_domain)
    evidence_id = _text(raw.get("evidence_id"))
    if not domain:
        raise CommonEvidenceContractError("producer_domain is required")
    if not evidence_id:
        raise CommonEvidenceContractError("evidence_id is required")

    source = _mapping(raw.get("source"))
    locator = _mapping(raw.get("locator"))
    locator_value = _mapping(locator.get("value"))
    metadata = _mapping(raw.get("metadata"))

    source_type = _text(_first(source.get("source_type"), raw.get("source_type")))
    source_id = _text(_first(source.get("source_id"), raw.get("source_id")))
    source_version = _text(
        _first(
            source.get("source_version"),
            source.get("revision"),
            raw.get("source_version"),
            raw.get("revision"),
            metadata.get("source_version"),
        )
    )
    source_reference = _text(
        _first(
            raw.get("source_reference"),
            raw.get("source_ref_uri"),
            source.get("uri"),
            source.get("source_ref"),
        )
    )
    source_ref = _text(raw.get("source_ref")) or _source_ref(source_id, source_version)

    page = _first(locator_value.get("page"), locator.get("page"), raw.get("page"))
    if page is not None:
        try:
            page = int(page)
        except (TypeError, ValueError) as exc:
            raise CommonEvidenceContractError("locator.page must be an integer") from exc
        if page < 1:
            raise CommonEvidenceContractError("locator.page must be >= 1")
    section = _text(_first(locator_value.get("section"), locator.get("section"), raw.get("section")))
    anchor = _text(
        _first(
            locator_value.get("source_anchor"),
            locator_value.get("anchor"),
            locator.get("source_anchor"),
            locator.get("anchor"),
            raw.get("anchor"),
        )
    )
    excerpt = _text(_first(raw.get("excerpt"), raw.get("excerpt_or_caption")))
    source_text = _text(raw.get("source_text"))
    content_hash = _text(
        _first(
            raw.get("content_hash"),
            source.get("content_hash"),
            locator_value.get("content_hash"),
        )
    )
    evidence_type = _text(raw.get("evidence_type"))
    if not evidence_type and excerpt is not None:
        evidence_type = "SOURCE_EXCERPT"
    if not evidence_type:
        raise CommonEvidenceContractError("evidence_type is required")

    created_at = _text(raw.get("created_at"))
    # No timestamp is fabricated: legacy objects without one remain nullable.
    verification_status = _text(
        _first(raw.get("verification_status"), metadata.get("verification_status"))
    )
    evidence_status = _text(
        _first(raw.get("evidence_status"), metadata.get("evidence_status"))
    )

    return {
        "contract_version": COMMON_EVIDENCE_CONTRACT_VERSION,
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "source": {
            "source_type": source_type,
            "source_id": source_id,
            "source_version": source_version,
        },
        "locator": {"page": page, "section": section, "anchor": anchor},
        "excerpt": excerpt,
        "source_text": source_text,
        "content_hash": content_hash,
        "source_ref": source_ref,
        "source_reference": source_reference,
        "producer_domain": domain,
        "producer_object_id": _text(producer_object_id),
        "producer_object_version": producer_object_version,
        "verification_status": verification_status,
        "evidence_status": evidence_status,
        "created_at": created_at,
    }


def validate_common_evidence(value: Mapping[str, Any]) -> None:
    """Perform the contract checks that are independent of JSON Schema tooling."""

    if value.get("contract_version") != COMMON_EVIDENCE_CONTRACT_VERSION:
        raise CommonEvidenceContractError("unsupported common evidence contract")
    for field in ("evidence_id", "evidence_type", "producer_domain"):
        if not _text(value.get(field)):
            raise CommonEvidenceContractError(f"{field} is required")
    source = _mapping(value.get("source"))
    if not all(key in source for key in ("source_type", "source_id", "source_version")):
        raise CommonEvidenceContractError("source contract is incomplete")
    locator = _mapping(value.get("locator"))
    if not all(key in locator for key in ("page", "section", "anchor")):
        raise CommonEvidenceContractError("locator contract is incomplete")
