"""Product integration for Hardware AI Retrieval.

This layer is intentionally additive. Formal Knowledge remains source of truth;
OpenSearch and retrieval metadata are rebuildable sidecars. The current Hardware
Case search API keeps its response shape while gaining natural-language query
understanding and deterministic fallbacks.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from services.hardware_knowledge_consumption import normalize_search_text


class HardwareCaseAIRetrievalError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class RetrievalQueryService(Protocol):
    def search(
        self,
        text: str = "",
        *,
        filters: Mapping[str, Any] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        ...


class RetrievalIndexer(Protocol):
    def rebuild_all(
        self,
        generation_id: str,
        items: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        ...


_STOP_PHRASES = tuple(
    sorted(
        {
            "帮我找一下",
            "帮我查一下",
            "请帮我找",
            "请帮我查",
            "这块",
            "以前出过什么",
            "有没有跟",
            "有关的",
            "有哪些",
            "有什么",
            "有没有",
            "哪一些",
            "哪些",
            "查一下",
            "查找",
            "搜索",
            "看看",
            "相关的",
            "相关",
            "历史案例",
            "案例",
            "历史",
            "问题",
            "故障",
            "请问",
            "帮我",
            "的",
        },
        key=len,
        reverse=True,
    )
)
_ENGLISH_STOP = {
    "what",
    "which",
    "show",
    "find",
    "search",
    "case",
    "cases",
    "issue",
    "issues",
    "problem",
    "problems",
    "related",
    "about",
    "please",
    "there",
    "are",
    "the",
    "a",
    "an",
}


def understand_hardware_query(text: str) -> dict[str, Any]:
    """Extract a high-signal engineering retrieval phrase from user wording.

    W2 V1 deliberately avoids inventing engineering facts. It only removes
    conversational scaffolding such as "有哪些...的问题" and preserves the
    remaining literal engineering terms.
    """

    raw = normalize_search_text(text)
    if not raw:
        return {
            "intent": "LIST_OR_SEARCH",
            "original_query": str(text or ""),
            "retrieval_text": "",
            "terms": [],
        }

    cleaned = raw
    for phrase in _STOP_PHRASES:
        cleaned = cleaned.replace(phrase, " ")
    cleaned = re.sub(r"[，。！？、；：,.!?;:()（）\[\]{}<>《》]+", " ", cleaned)
    parts = []
    for part in re.split(r"\s+", cleaned):
        token = part.strip()
        if not token or token in _ENGLISH_STOP:
            continue
        parts.append(token)
    parts = list(dict.fromkeys(parts))
    retrieval_text = " ".join(parts).strip() or raw

    return {
        "intent": "LIST_OR_SEARCH",
        "original_query": str(text or ""),
        "retrieval_text": retrieval_text,
        "terms": parts or [raw],
    }


class HardwareCaseAIRetrievalService:
    """Bridge AI retrieval hits back to consumer-visible Hardware Cases."""

    def __init__(
        self,
        case_service: Any,
        *,
        retrieval_query_service: RetrievalQueryService | None = None,
        consumption_service: Any | None = None,
    ) -> None:
        self.case_service = case_service
        self.retrieval_query_service = retrieval_query_service
        self.consumption_service = consumption_service

    @staticmethod
    def _case_lookup(items: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        lookup: dict[str, dict[str, Any]] = {}
        for item in items:
            row = dict(item)
            for key in ("case_id", "business_case_id"):
                value = str(row.get(key) or "").strip()
                if value:
                    lookup[value] = row
        return lookup

    @staticmethod
    def _error_code(error: Exception) -> str:
        return str(getattr(error, "code", None) or type(error).__name__)

    @staticmethod
    def _projection_to_case(
        projection: Mapping[str, Any],
        *,
        fallback_case_id: str = "",
    ) -> dict[str, Any]:
        business_case_id = str(
            projection.get("business_case_id") or fallback_case_id or ""
        ).strip()
        knowledge_id = str(projection.get("knowledge_id") or "").strip()
        case_id = business_case_id or knowledge_id
        facts = {
            field: projection.get(field)
            for field in (
                "symptom",
                "impact",
                "occurrence_condition",
                "analysis_process",
                "failure_mode",
                "root_cause",
                "failure_mechanism",
                "actions",
                "verification_result",
                "conclusion",
            )
            if projection.get(field) not in (None, "", [], {})
        }
        return {
            "case_id": case_id,
            "business_case_id": business_case_id or case_id,
            "knowledge_id": knowledge_id or None,
            "title": str(projection.get("title") or case_id or "Formal Knowledge"),
            "case_status": "PUBLISHED",
            "processing_status": "FORMAL_KNOWLEDGE",
            "product_context": {},
            "facts": facts,
            "mapping_paths": {
                "CIRCUIT_FEATURE": [],
                "MATERIAL_DEVICE": [],
            },
            "evidence_health": (
                "AVAILABLE" if projection.get("evidence_refs") else "FORMAL_ONLY"
            ),
            "published_at": projection.get("projected_at"),
            "updated_at": projection.get("projected_at"),
            "source_kind": "FORMAL_KNOWLEDGE",
            "formal_revision": projection.get("formal_revision"),
            "formal_object_hash": projection.get("formal_object_hash"),
            "interfaces": (
                [projection.get("interface")]
                if projection.get("interface")
                else []
            ),
            "signals": (
                [projection.get("signal")]
                if projection.get("signal")
                else []
            ),
            "device_refs": list(projection.get("device_refs") or []),
            "evidence_refs": list(projection.get("evidence_refs") or []),
        }

    def _resolve_formal_case(
        self,
        hit: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        projection: Mapping[str, Any] | None = None
        knowledge_id = str(hit.get("knowledge_id") or "").strip()
        business_case_id = str(hit.get("business_case_id") or "").strip()

        if self.consumption_service is not None and knowledge_id:
            try:
                candidate = self.consumption_service.get(knowledge_id)
                if isinstance(candidate, Mapping):
                    projection = candidate
            except Exception:
                projection = None

        if projection is None and self.consumption_service is not None and business_case_id:
            try:
                payload = self.consumption_service.search(
                    "",
                    business_case_id=business_case_id,
                    limit=2,
                )
                rows = payload.get("results") if isinstance(payload, Mapping) else None
                if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
                    projection = rows[0]
            except Exception:
                projection = None

        if projection is None:
            searchable_fields = {
                key: hit.get(key)
                for key in (
                    "knowledge_id",
                    "business_case_id",
                    "title",
                    "symptom",
                    "impact",
                    "occurrence_condition",
                    "analysis_process",
                    "failure_mode",
                    "root_cause",
                    "failure_mechanism",
                    "actions",
                    "verification_result",
                    "conclusion",
                    "interface",
                    "signal",
                    "device_refs",
                    "evidence_refs",
                    "formal_revision",
                    "formal_object_hash",
                    "projected_at",
                )
                if hit.get(key) not in (None, "", [], {})
            }
            if not searchable_fields:
                return None
            projection = searchable_fields

        return self._projection_to_case(
            projection,
            fallback_case_id=business_case_id,
        )

    def get_case(
        self,
        case_id: str,
        *,
        role: str = "CONSUMER",
        historical: bool = False,
    ) -> dict[str, Any]:
        try:
            return self.case_service.get_case(
                case_id,
                role=role,
                historical=historical,
            )
        except Exception as local_error:
            if self.consumption_service is None:
                raise local_error

        try:
            payload = self.consumption_service.search(
                "",
                business_case_id=case_id,
                limit=2,
            )
            rows = payload.get("results") if isinstance(payload, Mapping) else None
            if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
                return self._projection_to_case(rows[0], fallback_case_id=case_id)
            direct = self.consumption_service.get(case_id)
            if isinstance(direct, Mapping):
                return self._projection_to_case(direct, fallback_case_id=case_id)
        except Exception:
            pass
        raise HardwareCaseAIRetrievalError("CASE_NOT_FOUND")

    def _formal_projection_for_case_id(
        self,
        case_id: str,
    ) -> dict[str, Any] | None:
        if self.consumption_service is None:
            return None
        try:
            payload = self.consumption_service.search(
                "",
                business_case_id=case_id,
                limit=2,
            )
            rows = payload.get("results") if isinstance(payload, Mapping) else None
            if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], Mapping):
                return dict(rows[0])
            if isinstance(rows, list) and len(rows) > 1:
                raise HardwareCaseAIRetrievalError(
                    "FORMAL_PRODUCT_BINDING_AMBIGUOUS"
                )
            direct = self.consumption_service.get(case_id)
            if isinstance(direct, Mapping):
                return dict(direct)
        except HardwareCaseAIRetrievalError:
            raise
        except Exception:
            return None
        return None

    def _resolve_formal_evidence_rows(
        self,
        projection: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        refs = projection.get("evidence_refs")
        if not isinstance(refs, list):
            return []
        adapter = getattr(self.consumption_service, "adapter", None)
        case_id = str(projection.get("business_case_id") or "").strip()
        rows: list[dict[str, Any]] = []
        for evidence_id in refs:
            evidence_id = str(evidence_id)
            if adapter is None:
                rows.append(
                    {
                        "evidence_id": evidence_id,
                        "case_id": case_id,
                        "source_id": None,
                        "source_ref": None,
                        "evidence_type": "FORMAL_EVIDENCE",
                        "locator": {},
                        "excerpt_or_caption": None,
                        "evidence_status": "SOURCE_UNAVAILABLE",
                        "binding_mode": "FORMAL_KNOWLEDGE_DIRECT",
                    }
                )
                continue
            try:
                resolved = adapter.resolve_evidence(evidence_id)
            except Exception as error:
                rows.append(
                    {
                        "evidence_id": evidence_id,
                        "case_id": case_id,
                        "source_id": None,
                        "source_ref": None,
                        "evidence_type": "FORMAL_EVIDENCE",
                        "locator": {},
                        "excerpt_or_caption": None,
                        "evidence_status": "SOURCE_UNAVAILABLE",
                        "binding_mode": "FORMAL_KNOWLEDGE_DIRECT",
                        "resolution_error": self._error_code(error),
                    }
                )
                continue
            source = resolved.get("source") if isinstance(resolved, Mapping) else None
            metadata = source.get("metadata") if isinstance(source, Mapping) else None
            locator = (
                metadata.get("hardware_locator")
                if isinstance(metadata, Mapping)
                else None
            )
            source_id = (
                str(source.get("source_id"))
                if isinstance(source, Mapping) and source.get("source_id")
                else None
            )
            source_ref = (
                str(source.get("uri"))
                if isinstance(source, Mapping) and source.get("uri")
                else None
            )
            available = bool(
                source_id and source_ref and isinstance(locator, Mapping)
            )
            rows.append(
                {
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                    "source_id": source_id,
                    "source_ref": source_ref,
                    "evidence_type": str(
                        resolved.get("evidence_type")
                        or (
                            source.get("source_type")
                            if isinstance(source, Mapping)
                            else ""
                        )
                        or "FORMAL_EVIDENCE"
                    ),
                    "locator": dict(locator) if isinstance(locator, Mapping) else {},
                    "excerpt_or_caption": (
                        resolved.get("excerpt")
                        if isinstance(resolved, Mapping)
                        else None
                    ),
                    "evidence_status": (
                        "AVAILABLE" if available else "SOURCE_UNAVAILABLE"
                    ),
                    "binding_mode": "FORMAL_KNOWLEDGE_DIRECT",
                }
            )
        return rows

    def get_evidence(
        self,
        case_id: str,
        *,
        role: str = "CONSUMER",
        historical: bool = False,
    ) -> dict[str, Any]:
        try:
            return self.case_service.get_evidence(
                case_id,
                role=role,
                historical=historical,
            )
        except Exception as local_error:
            if self.consumption_service is None:
                raise local_error

        projection = self._formal_projection_for_case_id(case_id)
        if projection is None:
            raise HardwareCaseAIRetrievalError("CASE_NOT_FOUND")
        return {
            "contract_version": "hardware-case/v1",
            "case_id": str(
                projection.get("business_case_id") or case_id
            ),
            "knowledge_id": projection.get("knowledge_id"),
            "evidence": self._resolve_formal_evidence_rows(projection),
            "formal_evidence_refs": list(
                projection.get("evidence_refs") or []
            ),
            "formal_only": True,
            "binding_mode": "FORMAL_KNOWLEDGE_DIRECT",
        }

    def _visible_payload(
        self,
        *,
        role: str,
        statuses: Any,
        historical: bool,
    ) -> dict[str, Any]:
        payload = self.case_service.search_cases(
            "",
            role=role,
            statuses=statuses,
            historical=historical,
        )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
            raise HardwareCaseAIRetrievalError("HARDWARE_CASE_SEARCH_RESPONSE_INVALID")
        return dict(payload)

    @staticmethod
    def _attach_retrieval(
        case: Mapping[str, Any],
        *,
        mode: str,
        score: Any,
        why_hit: Mapping[str, Any] | None,
        knowledge_id: str | None = None,
    ) -> dict[str, Any]:
        row = dict(case)
        row["retrieval"] = {
            "mode": mode,
            "score": score,
            "knowledge_id": knowledge_id,
            "why_hit": dict(why_hit or {}),
        }
        return row

    def search_cases(
        self,
        query: str = "",
        *,
        role: str = "CONSUMER",
        statuses: Any = None,
        historical: bool = False,
    ) -> dict[str, Any]:
        raw_query = str(query or "")
        understood = understand_hardware_query(raw_query)

        if not raw_query.strip():
            payload = self.case_service.search_cases(
                raw_query,
                role=role,
                statuses=statuses,
                historical=historical,
            )
            result = dict(payload)
            result["retrieval"] = {
                "mode": "LEGACY",
                "query_understanding": understood,
                "degraded": False,
                "errors": [],
            }
            return result

        visible = self._visible_payload(
            role=role,
            statuses=statuses,
            historical=historical,
        )
        lookup = self._case_lookup(visible["results"])
        errors: list[str] = []
        retrieval_text = str(understood["retrieval_text"])

        if self.retrieval_query_service is not None:
            try:
                payload = self.retrieval_query_service.search(
                    retrieval_text,
                    limit=100,
                )
                hits = payload.get("results")
                if not isinstance(hits, list):
                    raise HardwareCaseAIRetrievalError(
                        "RETRIEVAL_QUERY_RESPONSE_INVALID"
                    )
                mapped: list[dict[str, Any]] = []
                seen: set[str] = set()
                for hit in hits:
                    if not isinstance(hit, Mapping):
                        continue
                    business_case_id = str(hit.get("business_case_id") or "").strip()
                    case = lookup.get(business_case_id)
                    if case is None:
                        case = self._resolve_formal_case(hit)
                    if case is None:
                        continue
                    case_id = str(case.get("case_id") or business_case_id)
                    if case_id in seen:
                        continue
                    seen.add(case_id)
                    mapped.append(
                        self._attach_retrieval(
                            case,
                            mode="OPENSEARCH",
                            score=hit.get("score"),
                            why_hit=hit.get("why_hit")
                            if isinstance(hit.get("why_hit"), Mapping)
                            else None,
                            knowledge_id=str(hit.get("knowledge_id") or "") or None,
                        )
                    )
                if mapped:
                    result = dict(visible)
                    result["results"] = mapped
                    result["retrieval"] = {
                        "mode": "OPENSEARCH",
                        "query_understanding": understood,
                        "degraded": False,
                        "errors": errors,
                    }
                    return result
            except Exception as error:
                errors.append(self._error_code(error))

        if self.consumption_service is not None:
            try:
                projection_payload = self.consumption_service.search(
                    retrieval_text,
                    limit=100,
                )
                hits = projection_payload.get("results")
                if not isinstance(hits, list):
                    raise HardwareCaseAIRetrievalError(
                        "CONSUMPTION_SEARCH_RESPONSE_INVALID"
                    )
                mapped = []
                seen: set[str] = set()
                for hit in hits:
                    if not isinstance(hit, Mapping):
                        continue
                    business_case_id = str(hit.get("business_case_id") or "").strip()
                    case = lookup.get(business_case_id)
                    if case is None:
                        case = self._resolve_formal_case(hit)
                    if case is None:
                        continue
                    case_id = str(case.get("case_id") or business_case_id)
                    if case_id in seen:
                        continue
                    seen.add(case_id)
                    mapped.append(
                        self._attach_retrieval(
                            case,
                            mode="SQLITE_FORMAL",
                            score=hit.get("match_score"),
                            why_hit={
                                "status": "FORMAL_PROJECTION_MATCH",
                                "claim_safe": True,
                                "reasons": list(hit.get("match_reasons") or []),
                            },
                            knowledge_id=str(hit.get("knowledge_id") or "") or None,
                        )
                    )
                if mapped:
                    result = dict(visible)
                    result["results"] = mapped
                    result["retrieval"] = {
                        "mode": "SQLITE_FORMAL",
                        "query_understanding": understood,
                        "degraded": self.retrieval_query_service is not None,
                        "errors": errors,
                    }
                    return result
            except Exception as error:
                errors.append(self._error_code(error))

        legacy = self.case_service.search_cases(
            retrieval_text,
            role=role,
            statuses=statuses,
            historical=historical,
        )
        result = dict(legacy)
        result["retrieval"] = {
            "mode": "LEGACY_NORMALIZED",
            "query_understanding": understood,
            "degraded": bool(errors),
            "errors": errors,
        }
        return result


class HardwareRetrievalCatalogService:
    """Explicit maintenance rebuild: Formal projection -> Tagger -> OpenSearch."""

    def __init__(
        self,
        consumption_service: Any,
        *,
        tagger_factory: Callable[[], Any],
        indexer: RetrievalIndexer,
    ) -> None:
        self.consumption_service = consumption_service
        self.tagger_factory = tagger_factory
        self.indexer = indexer

    @staticmethod
    def _generation_id() -> str:
        return datetime.now(timezone.utc).strftime("g%Y%m%dT%H%M%S%fZ")

    def rebuild_all(self, generation_id: str | None = None) -> dict[str, Any]:
        try:
            projections = self.consumption_service.store.list_all()
        except Exception as error:
            raise HardwareCaseAIRetrievalError(
                str(getattr(error, "code", None) or "CONSUMPTION_PROJECTION_READ_FAILED")
            ) from error
        try:
            tagger = self.tagger_factory()
        except Exception as error:
            raise HardwareCaseAIRetrievalError(
                str(getattr(error, "code", None) or "RETRIEVAL_TAGGER_UNAVAILABLE")
            ) from error

        items: list[dict[str, Any]] = []
        for projection in projections:
            try:
                metadata = tagger.tag(projection)
            except Exception as error:
                raise HardwareCaseAIRetrievalError(
                    str(getattr(error, "code", None) or "RETRIEVAL_TAGGING_FAILED")
                ) from error
            items.append(
                {
                    "projection": projection,
                    "metadata": metadata,
                }
            )

        generation = str(generation_id or self._generation_id()).strip()
        try:
            indexed = self.indexer.rebuild_all(generation, items)
        except Exception as error:
            raise HardwareCaseAIRetrievalError(
                str(getattr(error, "code", None) or "RETRIEVAL_INDEX_REBUILD_FAILED")
            ) from error
        return {
            "status": "PASS",
            "generation_id": generation,
            "projected_count": len(projections),
            "tagged_count": len(items),
            "index": indexed,
            "formal_knowledge_write": False,
        }


__all__ = [
    "HardwareCaseAIRetrievalError",
    "HardwareCaseAIRetrievalService",
    "HardwareRetrievalCatalogService",
    "understand_hardware_query",
]
