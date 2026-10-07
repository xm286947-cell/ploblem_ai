"""OpenSearch index document contract for Hardware AI Retrieval W1C.

Formal Knowledge remains the source of truth.  This layer converts the frozen
Hardware Knowledge consumption projection plus validated W1 retrieval metadata
into a rebuildable search-engine document.  Derived EXPANSION tags are isolated
to recall_text and can never become structured filters or claim-safe why-hit
facts.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol

from services.hardware_retrieval_metadata import (
    ALLOWED_TAG_SOURCE_FIELDS,
    EXPANSION,
    FACT,
    NORMALIZED,
    RANK_AND_EXPLAIN,
    RECALL_ONLY,
    RETRIEVAL_METADATA_CONTRACT_VERSION,
    build_retrieval_metadata,
    retrieval_source_values,
)

INDEX_DOCUMENT_CONTRACT_VERSION = "hardware-retrieval-index-document/v1"
INDEX_SCHEMA_VERSION = 1

_TEXT_FIELDS = tuple(
    field for field in ALLOWED_TAG_SOURCE_FIELDS
    if field not in {"key_parameters", "device_refs"}
)

HARDWARE_RETRIEVAL_INDEX_MAPPING: dict[str, Any] = {
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "contract_version": {"type": "keyword"},
            "index_schema_version": {"type": "integer"},
            "metadata_contract_version": {"type": "keyword"},
            "tagger_version": {"type": "keyword"},
            "knowledge_id": {"type": "keyword"},
            "public_ref": {"type": "keyword"},
            "business_case_id": {"type": "keyword"},
            "formal_revision": {"type": "integer"},
            "formal_object_hash": {"type": "keyword"},
            "formal_status": {"type": "keyword"},
            "source_domain": {"type": "keyword"},
            "source_object_type": {"type": "keyword"},
            "title": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "search_text": {"type": "text"},
            "recall_text": {"type": "text"},
            "source_fields": {
                "type": "object",
                "dynamic": "strict",
                "properties": {
                    **{field: {"type": "text"} for field in _TEXT_FIELDS},
                    "key_parameters": {"type": "text"},
                    "device_refs": {"type": "text"},
                },
            },
            "scope": {
                "type": "object",
                "dynamic": "strict",
                "properties": {
                    "interfaces": {"type": "keyword"},
                    "signals": {"type": "keyword"},
                    "devices": {"type": "keyword"},
                },
            },
            "fact_tags": {"type": "text"},
            "normalized_tags": {"type": "text"},
            "expansion_tags": {"type": "text"},
            "tag_provenance": {
                "type": "nested",
                "dynamic": "strict",
                "properties": {
                    "term": {"type": "text"},
                    "normalized_term": {"type": "keyword"},
                    "kind": {"type": "keyword"},
                    "usage": {"type": "keyword"},
                    "derived": {"type": "boolean"},
                    "claim_safe": {"type": "boolean"},
                    "source_term": {"type": "text"},
                    "source_fields": {"type": "keyword"},
                },
            },
        },
    },
}


class HardwareRetrievalIndexError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class SearchIndexAdapter(Protocol):
    def ensure_index(
        self, index_name: str, mapping: Mapping[str, Any]
    ) -> dict[str, Any]:
        ...

    def upsert(
        self,
        index_name: str,
        document_id: str,
        document: Mapping[str, Any],
        *,
        refresh: bool = True,
    ) -> dict[str, Any]:
        ...


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _validated_metadata(
    projection: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    if metadata.get("contract_version") != RETRIEVAL_METADATA_CONTRACT_VERSION:
        raise HardwareRetrievalIndexError("RETRIEVAL_METADATA_CONTRACT_INVALID")
    tags = metadata.get("tags")
    if not isinstance(tags, list):
        raise HardwareRetrievalIndexError("RETRIEVAL_METADATA_TAGS_INVALID")

    candidates: list[dict[str, Any]] = []
    for tag in tags:
        if not isinstance(tag, Mapping):
            raise HardwareRetrievalIndexError("RETRIEVAL_METADATA_TAG_INVALID")
        candidates.append(
            {
                "term": tag.get("term"),
                "kind": tag.get("kind"),
                "source_term": tag.get("source_term"),
                "source_fields": tag.get("source_fields"),
            }
        )
    try:
        expected = build_retrieval_metadata(projection, candidates)
    except Exception as error:
        code = str(getattr(error, "code", None) or "RETRIEVAL_METADATA_INVALID")
        raise HardwareRetrievalIndexError(code) from error
    if dict(metadata) != expected:
        raise HardwareRetrievalIndexError("RETRIEVAL_METADATA_MISMATCH")
    return expected


def build_index_document(
    projection: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    validated = _validated_metadata(projection, metadata)

    public_ref = str(projection.get("public_ref") or "").strip()
    formal_status = str(projection.get("formal_status") or "").strip()
    if not public_ref or formal_status != "ACTIVE":
        raise HardwareRetrievalIndexError("RETRIEVAL_INDEX_SOURCE_INVALID")

    source_fields: dict[str, str] = {}
    formal_terms: list[str] = []
    for field in ALLOWED_TAG_SOURCE_FIELDS:
        try:
            values = retrieval_source_values(projection, field)
        except Exception as error:
            code = str(getattr(error, "code", None) or "RETRIEVAL_SOURCE_INVALID")
            raise HardwareRetrievalIndexError(code) from error
        text = " | ".join(values)
        source_fields[field] = text
        formal_terms.extend(values)

    fact_tags: list[str] = []
    normalized_tags: list[str] = []
    expansion_tags: list[str] = []
    rank_terms: list[str] = []
    for tag in validated["tags"]:
        kind = tag["kind"]
        term = str(tag["term"])
        if kind == FACT:
            fact_tags.append(term)
        elif kind == NORMALIZED:
            normalized_tags.append(term)
        elif kind == EXPANSION:
            expansion_tags.append(term)
        else:
            raise HardwareRetrievalIndexError("RETRIEVAL_TAG_KIND_INVALID")

        if tag["usage"] == RANK_AND_EXPLAIN:
            if tag["derived"] or not tag["claim_safe"]:
                raise HardwareRetrievalIndexError("RETRIEVAL_CLAIM_BOUNDARY_INVALID")
            rank_terms.append(term)
        elif tag["usage"] == RECALL_ONLY:
            if not tag["derived"] or tag["claim_safe"]:
                raise HardwareRetrievalIndexError("RETRIEVAL_RECALL_BOUNDARY_INVALID")
        else:
            raise HardwareRetrievalIndexError("RETRIEVAL_TAG_USAGE_INVALID")

    search_terms = _ordered_unique([*formal_terms, *rank_terms])
    recall_terms = _ordered_unique([*search_terms, *expansion_tags])
    scope = validated["scope"]

    return {
        "contract_version": INDEX_DOCUMENT_CONTRACT_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "metadata_contract_version": validated["contract_version"],
        "tagger_version": validated["tagger_version"],
        "knowledge_id": str(projection["knowledge_id"]),
        "public_ref": public_ref,
        "business_case_id": str(projection["business_case_id"]),
        "formal_revision": int(projection["formal_revision"]),
        "formal_object_hash": str(projection["formal_object_hash"]),
        "formal_status": formal_status,
        "source_domain": str(projection["source_domain"]),
        "source_object_type": str(projection["source_object_type"]),
        "title": source_fields.get("title") or "",
        "search_text": " ".join(search_terms),
        "recall_text": " ".join(recall_terms),
        "source_fields": source_fields,
        "scope": {
            "interfaces": list(scope["interfaces"]),
            "signals": list(scope["signals"]),
            "devices": list(scope["devices"]),
        },
        "fact_tags": _ordered_unique(fact_tags),
        "normalized_tags": _ordered_unique(normalized_tags),
        "expansion_tags": _ordered_unique(expansion_tags),
        "tag_provenance": [dict(tag) for tag in validated["tags"]],
    }


class HardwareRetrievalIndexWriter:
    """Write one rebuildable generation using the W0 HTTP adapter contract."""

    def __init__(self, adapter: SearchIndexAdapter, *, index_name: str):
        name = str(index_name or "").strip()
        if not name:
            raise HardwareRetrievalIndexError("RETRIEVAL_INDEX_NAME_REQUIRED")
        self.adapter = adapter
        self.index_name = name

    def write(
        self,
        projection: Mapping[str, Any],
        metadata: Mapping[str, Any],
        *,
        refresh: bool = True,
    ) -> dict[str, Any]:
        document = build_index_document(projection, metadata)
        ensured = self.adapter.ensure_index(
            self.index_name,
            HARDWARE_RETRIEVAL_INDEX_MAPPING,
        )
        upserted = self.adapter.upsert(
            self.index_name,
            str(document["knowledge_id"]),
            document,
            refresh=refresh,
        )
        return {
            "index_name": self.index_name,
            "knowledge_id": document["knowledge_id"],
            "index": ensured,
            "upsert": upserted,
            "document": document,
        }


__all__ = [
    "HARDWARE_RETRIEVAL_INDEX_MAPPING",
    "INDEX_DOCUMENT_CONTRACT_VERSION",
    "INDEX_SCHEMA_VERSION",
    "HardwareRetrievalIndexError",
    "HardwareRetrievalIndexWriter",
    "build_index_document",
]
