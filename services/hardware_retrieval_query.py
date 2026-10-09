"""BM25 + structured filter + WHY_HIT query layer for Hardware Retrieval W1D."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from services.hardware_knowledge_consumption import normalize_search_text
from services.hardware_retrieval_index import (
    INDEX_DOCUMENT_CONTRACT_VERSION,
    INDEX_SCHEMA_VERSION,
)

QUERY_RESULT_CONTRACT_VERSION = "hardware-retrieval-query-result/v1"

SEARCH_FIELDS: tuple[str, ...] = (
    "search_text^4",
    "recall_text",
)

FILTER_FIELD_MAP = {
    "business_case_id": "business_case_id",
    "interface": "scope.interfaces",
    "signal": "scope.signals",
    "device": "scope.devices",
}


class HardwareRetrievalQueryError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class SearchAdapter(Protocol):
    def search(
        self,
        index_or_alias: str,
        text: str = "",
        *,
        filters: Mapping[str, Any] | None = None,
        limit: int = 20,
        fields: Sequence[str] = (),
    ) -> dict[str, Any]:
        ...


def _query_terms(text: str) -> list[str]:
    normalized = normalize_search_text(text)
    return list(dict.fromkeys(part for part in normalized.split(" ") if part))


def _contains(value: Any, term: str) -> bool:
    return bool(term) and term in normalize_search_text(value)


def _validate_source(source: Mapping[str, Any]) -> None:
    if (
        source.get("contract_version") != INDEX_DOCUMENT_CONTRACT_VERSION
        or source.get("index_schema_version") != INDEX_SCHEMA_VERSION
        or source.get("formal_status") != "ACTIVE"
        or source.get("source_domain") != "HARDWARE_CASE"
        or source.get("source_object_type") != "HARDWARE_CASE"
        or not str(source.get("knowledge_id") or "").strip()
        or not isinstance(source.get("source_fields"), Mapping)
        or not isinstance(source.get("scope"), Mapping)
        or not isinstance(source.get("tag_provenance"), list)
    ):
        raise HardwareRetrievalQueryError("RETRIEVAL_HIT_CONTRACT_INVALID")


def _claim_safe_reason(term: str, source: Mapping[str, Any]) -> dict[str, Any] | None:
    fields = source.get("source_fields") or {}
    for field in sorted(fields):
        value = fields.get(field)
        if value and _contains(value, term):
            return {
                "query_term": term,
                "reason_type": "FORMAL_FIELD",
                "field": field,
                "matched_value": str(value),
                "claim_safe": True,
            }

    for field, reason_type in (
        ("fact_tags", "FACT_TAG"),
        ("normalized_tags", "NORMALIZED_TAG"),
    ):
        values = source.get(field) or []
        if not isinstance(values, list):
            raise HardwareRetrievalQueryError("RETRIEVAL_HIT_CONTRACT_INVALID")
        for value in values:
            if _contains(value, term):
                return {
                    "query_term": term,
                    "reason_type": reason_type,
                    "matched_value": str(value),
                    "claim_safe": True,
                }
    return None


def _expansion_reason(term: str, source: Mapping[str, Any]) -> dict[str, Any] | None:
    tags = source.get("tag_provenance") or []
    for tag in tags:
        if not isinstance(tag, Mapping):
            raise HardwareRetrievalQueryError("RETRIEVAL_HIT_CONTRACT_INVALID")
        if tag.get("kind") != "EXPANSION":
            continue
        if (
            tag.get("usage") != "RECALL_ONLY"
            or tag.get("derived") is not True
            or tag.get("claim_safe") is not False
        ):
            raise HardwareRetrievalQueryError("RETRIEVAL_HIT_CLAIM_BOUNDARY_INVALID")
        if _contains(tag.get("term"), term):
            return {
                "query_term": term,
                "reason_type": "EXPANSION_RECALL",
                "matched_value": str(tag.get("term") or ""),
                "claim_safe": False,
                "source_term": str(tag.get("source_term") or ""),
                "source_fields": list(tag.get("source_fields") or []),
            }
    return None


def build_why_hit(text: str, source: Mapping[str, Any]) -> dict[str, Any]:
    _validate_source(source)
    terms = _query_terms(text)
    if not terms:
        return {
            "status": "FILTER_ONLY",
            "claim_safe": True,
            "reasons": [],
        }

    reasons: list[dict[str, Any]] = []
    unexplained: list[str] = []
    expansion_used = False
    for term in terms:
        reason = _claim_safe_reason(term, source)
        if reason is None:
            reason = _expansion_reason(term, source)
        if reason is None:
            unexplained.append(term)
            continue
        reasons.append(reason)
        if reason["claim_safe"] is False:
            expansion_used = True

    if unexplained:
        status = "PARTIAL_EXPLANATION" if reasons else "UNEXPLAINED_ENGINE_MATCH"
    elif expansion_used:
        status = "EXPANSION_ASSISTED"
    else:
        status = "CLAIM_SAFE_MATCH"

    return {
        "status": status,
        "claim_safe": not expansion_used and not unexplained,
        "reasons": reasons,
        "unexplained_terms": unexplained,
    }


class HardwareRetrievalQueryService:
    """Thin query layer over the W0 HTTP adapter."""

    def __init__(self, adapter: SearchAdapter, *, index_alias: str):
        alias = str(index_alias or "").strip()
        if not alias:
            raise HardwareRetrievalQueryError("RETRIEVAL_ALIAS_REQUIRED")
        self.adapter = adapter
        self.index_alias = alias

    @staticmethod
    def _engine_filters(filters: Mapping[str, Any] | None) -> dict[str, Any]:
        supplied = dict(filters or {})
        unknown = sorted(set(supplied) - set(FILTER_FIELD_MAP))
        if unknown:
            raise HardwareRetrievalQueryError("RETRIEVAL_FILTER_INVALID")

        result: dict[str, Any] = {
            "formal_status": "ACTIVE",
            "source_domain": "HARDWARE_CASE",
            "source_object_type": "HARDWARE_CASE",
        }
        for name, value in supplied.items():
            if value in (None, "", []):
                continue
            result[FILTER_FIELD_MAP[name]] = value
        return result

    def search(
        self,
        text: str = "",
        *,
        filters: Mapping[str, Any] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if int(limit) < 1 or int(limit) > 100:
            raise HardwareRetrievalQueryError("RETRIEVAL_LIMIT_INVALID")
        engine_filters = self._engine_filters(filters)
        response = self.adapter.search(
            self.index_alias,
            str(text or ""),
            filters=engine_filters,
            limit=int(limit),
            fields=SEARCH_FIELDS,
        )
        hits = response.get("hits")
        if not isinstance(hits, list):
            raise HardwareRetrievalQueryError("RETRIEVAL_ENGINE_RESPONSE_INVALID")

        results: list[dict[str, Any]] = []
        for hit in hits:
            if not isinstance(hit, Mapping):
                raise HardwareRetrievalQueryError("RETRIEVAL_ENGINE_RESPONSE_INVALID")
            source = hit.get("_source")
            if not isinstance(source, Mapping):
                raise HardwareRetrievalQueryError("RETRIEVAL_HIT_SOURCE_MISSING")
            _validate_source(source)
            why_hit = build_why_hit(str(text or ""), source)
            results.append(
                {
                    "knowledge_id": str(source["knowledge_id"]),
                    "business_case_id": str(source.get("business_case_id") or ""),
                    "title": str(source.get("title") or ""),
                    "score": hit.get("_score"),
                    "why_hit": why_hit,
                }
            )

        return {
            "contract_version": QUERY_RESULT_CONTRACT_VERSION,
            "index_alias": self.index_alias,
            "query": str(text or ""),
            "filters": dict(filters or {}),
            "engine_filters": engine_filters,
            "results": results,
        }


__all__ = [
    "FILTER_FIELD_MAP",
    "QUERY_RESULT_CONTRACT_VERSION",
    "SEARCH_FIELDS",
    "HardwareRetrievalQueryError",
    "HardwareRetrievalQueryService",
    "build_why_hit",
]
