"""Derived retrieval metadata over the frozen Hardware Knowledge consumption projection.

Formal Knowledge remains the source of truth.  This module accepts only the
rebuildable read projection and validates untrusted AI tag suggestions before
they can become search metadata.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Mapping, Sequence

from services.hardware_knowledge_consumption import (
    CONSUMPTION_CONTRACT_VERSION,
    PROJECTION_SCHEMA_VERSION,
    normalize_search_text,
)

RETRIEVAL_METADATA_CONTRACT_VERSION = "hardware-retrieval-metadata/v1"
RETRIEVAL_TAGGER_VERSION = "hardware-retrieval-tagger/w1-v1"

FACT = "FACT"
NORMALIZED = "NORMALIZED"
EXPANSION = "EXPANSION"
TAG_KINDS = (FACT, NORMALIZED, EXPANSION)

RANK_AND_EXPLAIN = "RANK_AND_EXPLAIN"
RECALL_ONLY = "RECALL_ONLY"

_TEXT_SOURCE_FIELDS = (
    "title",
    "symptom",
    "occurrence_condition",
    "failure_mode",
    "root_cause",
    "failure_mechanism",
    "analysis_process",
    "actions",
    "verification_result",
    "engineering_rule",
    "design_constraint",
    "diagnostic_clue",
    "verification_method",
    "applicability",
    "conclusion",
    "interface",
    "signal",
)
_COMPLEX_SOURCE_FIELDS = ("key_parameters", "device_refs")
ALLOWED_TAG_SOURCE_FIELDS = _TEXT_SOURCE_FIELDS + _COMPLEX_SOURCE_FIELDS

_KEY_PARAMETER_VALUE_KEYS = (
    "name",
    "parameter",
    "parameter_name",
    "key",
    "value",
    "unit",
    "min",
    "max",
    "typical",
    "range",
    "condition",
)
_DEVICE_VALUE_KEYS = (
    "category",
    "generic_name_or_series",
    "internal_material_no",
    "manufacturer",
    "manufacturer_part_no",
)
_MAX_TAGS = 128
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_KIND_ORDER = {FACT: 0, NORMALIZED: 1, EXPANSION: 2}


class HardwareRetrievalMetadataError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _require_projection(row: Mapping[str, Any]) -> None:
    if (
        row.get("projection_schema_version") != PROJECTION_SCHEMA_VERSION
        or not str(row.get("knowledge_id") or "").strip()
        or not str(row.get("business_case_id") or "").strip()
        or row.get("source_domain") != "HARDWARE_CASE"
        or row.get("source_object_type") != "HARDWARE_CASE"
        or row.get("formal_status") != "ACTIVE"
        or not isinstance(row.get("formal_revision"), int)
        or isinstance(row.get("formal_revision"), bool)
        or int(row["formal_revision"]) < 1
        or not _HASH_PATTERN.fullmatch(str(row.get("formal_object_hash") or ""))
    ):
        raise HardwareRetrievalMetadataError("RETRIEVAL_SOURCE_INVALID")


def _append_scalar(values: list[str], value: Any) -> None:
    if value is None or value == "":
        return
    if isinstance(value, bool):
        values.append(str(value).lower())
        return
    if isinstance(value, (str, int, float)):
        values.append(str(value))
        return
    raise HardwareRetrievalMetadataError("RETRIEVAL_SOURCE_FIELD_INVALID")


def retrieval_source_values(row: Mapping[str, Any], field: str) -> list[str]:
    """Return only explicit Formal-derived values eligible to anchor a tag."""
    if field not in ALLOWED_TAG_SOURCE_FIELDS:
        raise HardwareRetrievalMetadataError("TAG_SOURCE_FIELD_INVALID")

    if field in _TEXT_SOURCE_FIELDS:
        value = row.get(field)
        if value in (None, ""):
            return []
        if not isinstance(value, str):
            raise HardwareRetrievalMetadataError("RETRIEVAL_SOURCE_FIELD_INVALID")
        return [value]

    if field == "key_parameters":
        source = row.get(field) or []
        if not isinstance(source, list):
            raise HardwareRetrievalMetadataError("RETRIEVAL_SOURCE_FIELD_INVALID")
        values: list[str] = []
        for item in source:
            if not isinstance(item, Mapping):
                raise HardwareRetrievalMetadataError("RETRIEVAL_SOURCE_FIELD_INVALID")
            for key in _KEY_PARAMETER_VALUE_KEYS:
                if key in item:
                    _append_scalar(values, item.get(key))
        return list(dict.fromkeys(values))

    source = row.get("device_refs") or []
    if not isinstance(source, list):
        raise HardwareRetrievalMetadataError("RETRIEVAL_SOURCE_FIELD_INVALID")
    values = []
    for item in source:
        if not isinstance(item, Mapping):
            raise HardwareRetrievalMetadataError("RETRIEVAL_SOURCE_FIELD_INVALID")
        status = item.get("status")
        if status not in {"EXPLICIT", "MISSING"}:
            raise HardwareRetrievalMetadataError("RETRIEVAL_SOURCE_FIELD_INVALID")
        if status != "EXPLICIT":
            continue
        for key in _DEVICE_VALUE_KEYS:
            value = item.get(key)
            if value is not None and not isinstance(value, str):
                raise HardwareRetrievalMetadataError("RETRIEVAL_SOURCE_FIELD_INVALID")
            _append_scalar(values, value)
    return list(dict.fromkeys(values))


def _mechanical_key(value: Any) -> str:
    # Mechanical normalization only: NFKC/case/whitespace plus punctuation removal.
    # Semantic aliases are EXPANSION, never NORMALIZED.
    return re.sub(r"[\W_]+", "", normalize_search_text(value), flags=re.UNICODE)


def _source_term_supported(source_term: str, values: Sequence[str]) -> bool:
    wanted = normalize_search_text(source_term)
    return bool(wanted) and any(
        wanted in normalize_search_text(value)
        for value in values
        if normalize_search_text(value)
    )


def _validate_tag(
    row: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    term = str(candidate.get("term") or "").strip()
    if not term:
        raise HardwareRetrievalMetadataError("TAG_TERM_REQUIRED")

    kind = str(candidate.get("kind") or "").strip().upper()
    if kind not in TAG_KINDS:
        raise HardwareRetrievalMetadataError("TAG_KIND_INVALID")

    source_fields_raw = candidate.get("source_fields")
    if (
        not isinstance(source_fields_raw, list)
        or not source_fields_raw
        or any(not isinstance(item, str) for item in source_fields_raw)
    ):
        raise HardwareRetrievalMetadataError("TAG_SOURCE_FIELDS_REQUIRED")
    source_fields = sorted(set(str(item).strip() for item in source_fields_raw))
    if (
        any(not item for item in source_fields)
        or any(item not in ALLOWED_TAG_SOURCE_FIELDS for item in source_fields)
    ):
        raise HardwareRetrievalMetadataError("TAG_SOURCE_FIELD_INVALID")

    values: list[str] = []
    for field in source_fields:
        values.extend(retrieval_source_values(row, field))
    values = list(dict.fromkeys(values))
    if not values:
        raise HardwareRetrievalMetadataError("TAG_SOURCE_EMPTY")

    source_term = str(candidate.get("source_term") or term).strip()
    if not source_term or not _source_term_supported(source_term, values):
        raise HardwareRetrievalMetadataError("TAG_SOURCE_TERM_UNSUPPORTED")

    if kind == FACT:
        if normalize_search_text(term) != normalize_search_text(source_term):
            raise HardwareRetrievalMetadataError("TAG_FACT_NOT_LITERAL")
        usage = RANK_AND_EXPLAIN
        derived = False
        claim_safe = True
    elif kind == NORMALIZED:
        if _mechanical_key(term) != _mechanical_key(source_term):
            raise HardwareRetrievalMetadataError("TAG_NORMALIZED_NOT_EQUIVALENT")
        usage = RANK_AND_EXPLAIN
        derived = False
        claim_safe = True
    else:
        usage = RECALL_ONLY
        derived = True
        claim_safe = False

    normalized_term = normalize_search_text(term)
    if not normalized_term:
        raise HardwareRetrievalMetadataError("TAG_TERM_REQUIRED")

    return {
        "term": term,
        "normalized_term": normalized_term,
        "kind": kind,
        "usage": usage,
        "derived": derived,
        "claim_safe": claim_safe,
        "source_term": source_term,
        "source_fields": source_fields,
    }


def _scope(row: Mapping[str, Any]) -> dict[str, Any]:
    interface = retrieval_source_values(row, "interface")
    signal = retrieval_source_values(row, "signal")
    devices = retrieval_source_values(row, "device_refs")
    return {
        "business_case_id": str(row["business_case_id"]),
        "source_domain": str(row["source_domain"]),
        "source_object_type": str(row["source_object_type"]),
        "interfaces": sorted(set(interface), key=normalize_search_text),
        "signals": sorted(set(signal), key=normalize_search_text),
        "devices": sorted(set(devices), key=normalize_search_text),
    }


def build_retrieval_metadata(
    projection: Mapping[str, Any],
    tag_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate untrusted tagger output into deterministic derived metadata."""
    _require_projection(projection)
    if not isinstance(tag_candidates, Sequence) or isinstance(
        tag_candidates, (str, bytes)
    ):
        raise HardwareRetrievalMetadataError("TAG_CANDIDATES_INVALID")
    if len(tag_candidates) > _MAX_TAGS:
        raise HardwareRetrievalMetadataError("TAG_LIMIT_EXCEEDED")

    validated = []
    for candidate in tag_candidates:
        if not isinstance(candidate, Mapping):
            raise HardwareRetrievalMetadataError("TAG_CANDIDATE_INVALID")
        validated.append(_validate_tag(projection, candidate))

    # Exact duplicate suggestions are harmless but collapse deterministically.
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for tag in validated:
        key = (
            tag["kind"],
            tag["normalized_term"],
            normalize_search_text(tag["source_term"]),
            tuple(tag["source_fields"]),
        )
        unique.setdefault(key, tag)
    tags = sorted(
        unique.values(),
        key=lambda item: (
            _KIND_ORDER[item["kind"]],
            item["normalized_term"],
            normalize_search_text(item["source_term"]),
            tuple(item["source_fields"]),
        ),
    )
    counts = Counter(item["kind"] for item in tags)

    return {
        "contract_version": RETRIEVAL_METADATA_CONTRACT_VERSION,
        "tagger_version": RETRIEVAL_TAGGER_VERSION,
        "knowledge_id": str(projection["knowledge_id"]),
        "source": {
            "contract_version": CONSUMPTION_CONTRACT_VERSION,
            "projection_schema_version": int(projection["projection_schema_version"]),
            "formal_revision": int(projection["formal_revision"]),
            "formal_object_hash": str(projection["formal_object_hash"]),
        },
        "scope": _scope(projection),
        "tags": tags,
        "tag_counts": {
            FACT: int(counts.get(FACT, 0)),
            NORMALIZED: int(counts.get(NORMALIZED, 0)),
            EXPANSION: int(counts.get(EXPANSION, 0)),
        },
    }


__all__ = [
    "ALLOWED_TAG_SOURCE_FIELDS",
    "EXPANSION",
    "FACT",
    "HardwareRetrievalMetadataError",
    "NORMALIZED",
    "RECALL_ONLY",
    "RETRIEVAL_METADATA_CONTRACT_VERSION",
    "RETRIEVAL_TAGGER_VERSION",
    "RANK_AND_EXPLAIN",
    "build_retrieval_metadata",
    "retrieval_source_values",
]
