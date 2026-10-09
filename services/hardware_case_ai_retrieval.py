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

# These aliases only create additional retrieval queries. They are never
# persisted to Formal Knowledge, used as structured filters, or treated as
# product facts. Keeping the alternatives separate is important because the
# Formal Consumption search requires every query token to be supported.
_ALIAS_TIER_RANK = {
    "LITERAL": 0,
    "DIRECT_SYNONYM": 1,
    "ENGINEERING_ALIAS": 2,
    "NARROW_SIGNAL_ALIAS": 3,
}
_ALIAS_TIER_COST = {
    "LITERAL": 0,
    "DIRECT_SYNONYM": 1,
    "ENGINEERING_ALIAS": 2,
    "NARROW_SIGNAL_ALIAS": 4,
}

# Each entry is (expanded term, relevance tier). Narrow signal tokens such as
# VCC and RESET_N are recall-only and intentionally carry the highest cost.
_RECALL_ONLY_ALIAS_GROUPS: tuple[
    tuple[str, tuple[tuple[str, str], ...]], ...
] = (
    ("单片机", (("mcu", "DIRECT_SYNONYM"),)),
    ("微控制器", (("mcu", "DIRECT_SYNONYM"),)),
    ("复位", (("reset", "DIRECT_SYNONYM"), ("reset_n", "NARROW_SIGNAL_ALIAS"))),
    ("reset_n", (("复位", "DIRECT_SYNONYM"), ("reset", "ENGINEERING_ALIAS"))),
    ("reset", (("复位", "DIRECT_SYNONYM"), ("reset_n", "NARROW_SIGNAL_ALIAS"))),
    ("供电", (("电源", "DIRECT_SYNONYM"), ("power", "ENGINEERING_ALIAS"), ("vcc", "NARROW_SIGNAL_ALIAS"))),
    ("电源", (("供电", "DIRECT_SYNONYM"), ("power", "ENGINEERING_ALIAS"), ("vcc", "NARROW_SIGNAL_ALIAS"))),
    ("power", (("供电", "DIRECT_SYNONYM"), ("电源", "DIRECT_SYNONYM"), ("vcc", "NARROW_SIGNAL_ALIAS"))),
    ("vcc", (("供电", "ENGINEERING_ALIAS"), ("电源", "ENGINEERING_ALIAS"), ("power", "ENGINEERING_ALIAS"))),
)


def _recall_only_query_variants(retrieval_text: str) -> list[dict[str, Any]]:
    """Return literal plus bounded synonym variants, with their provenance."""

    variants: list[dict[str, Any]] = [
        {"text": retrieval_text, "kind": "LITERAL", "rules": []}
    ]
    seen = {retrieval_text}
    normalized = retrieval_text.casefold()

    for trigger, alternatives in _RECALL_ONLY_ALIAS_GROUPS:
        if re.fullmatch(r"[a-z0-9_]+", trigger):
            trigger_pattern = rf"(?<![a-z0-9_]){re.escape(trigger)}(?![a-z0-9_])"
            matches_trigger = re.search(trigger_pattern, normalized) is not None
        else:
            trigger_pattern = re.escape(trigger)
            matches_trigger = trigger in normalized
        if not matches_trigger:
            continue
        current = list(variants)
        for variant in current:
            for alternative, tier in alternatives:
                if len(variants) >= 32:
                    break
                text = normalize_search_text(
                    re.sub(
                        trigger_pattern,
                        lambda _match: f" {alternative} ",
                        variant["text"],
                    )
                )
                if text == variant["text"] or text in seen:
                    continue
                seen.add(text)
                rule = {
                    "rule_id": f"RECALL_ALIAS_{trigger.upper()}",
                    "matched_phrase": trigger,
                    "expanded_term": alternative,
                    "use": "RECALL_ONLY",
                    "tier": tier,
                    "cost": _ALIAS_TIER_COST[tier],
                }
                rules = [*variant["rules"], rule]
                tier_rank = max(
                    (_ALIAS_TIER_RANK[item["tier"]] for item in rules),
                    default=0,
                )
                expansion_cost = sum(int(item["cost"]) for item in rules)
                variants.append(
                    {
                        "text": text,
                        "kind": "RECALL_ONLY",
                        "rules": rules,
                        "tier": next(
                            name for name, rank in _ALIAS_TIER_RANK.items()
                            if rank == tier_rank
                        ),
                        "priority_rank": tier_rank,
                        "expansion_cost": expansion_cost,
                    }
                )
            if len(variants) >= 32:
                break

    # The tokenized formal-search fallback requires each term to match.
    # Split only exact, short interface+symptom compounds; this does not relax
    # the ALL-terms constraint or invent a source fact.
    compound = re.fullmatch(
        r"(串口|uart|can|spi|i2c|adc)(乱码|丢包|失真|偏差|异常|故障)",
        normalized,
    )
    if compound:
        expanded = f"{compound.group(1)} {compound.group(2)}"
        if expanded not in seen:
            variants.append({
                "text": expanded,
                "kind": "RECALL_ONLY",
                "tier": "ENGINEERING_ALIAS",
                "priority_rank": _ALIAS_TIER_RANK["ENGINEERING_ALIAS"],
                "expansion_cost": _ALIAS_TIER_COST["ENGINEERING_ALIAS"],
                "rules": [{
                    "rule_id": "INTERFACE_SYMPTOM_COMPOUND_SPLIT",
                    "matched_phrase": retrieval_text,
                    "expanded_term": expanded,
                    "use": "RECALL_ONLY",
                    "tier": "ENGINEERING_ALIAS",
                    "cost": _ALIAS_TIER_COST["ENGINEERING_ALIAS"],
                }],
            })

    return sorted(
        variants,
        key=lambda item: (
            int(item.get("priority_rank", 0)),
            int(item.get("expansion_cost", 0)),
            str(item["text"]),
        ),
    )


def understand_hardware_query(text: str) -> dict[str, Any]:
    """Extract a high-signal engineering retrieval phrase from user wording.

    W2 V1 deliberately avoids inventing engineering facts. It only removes
    conversational scaffolding such as "有哪些...的问题" and preserves the
    remaining literal engineering terms.
    """

    raw = normalize_search_text(text)
    if not raw:
        variants = _recall_only_query_variants("")
        return {
            "intent": "LIST_OR_SEARCH",
            "original_query": str(text or ""),
            "retrieval_text": "",
            "terms": [],
            "search_queries": variants,
            "recall_only_expansions": [],
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
    variants = _recall_only_query_variants(retrieval_text)
    for variant in variants:
        variant["original_query"] = str(text or "")

    return {
        "intent": "LIST_OR_SEARCH",
        "original_query": str(text or ""),
        "retrieval_text": retrieval_text,
        "terms": parts or [raw],
        "search_queries": variants,
        "recall_only_expansions": [
            variant for variant in variants if variant["kind"] == "RECALL_ONLY"
        ],
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
        query_variant: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = dict(case)
        explanation = dict(why_hit or {})
        if query_variant:
            explanation["query_expansion"] = {
                "policy": str(query_variant.get("kind") or "LITERAL"),
                "original_query": query_variant.get("original_query"),
                "tier": str(query_variant.get("tier") or "LITERAL"),
                "priority_rank": int(query_variant.get("priority_rank") or 0),
                "expansion_cost": int(query_variant.get("expansion_cost") or 0),
                "rules": list(query_variant.get("rules") or []),
            }
        row["retrieval"] = {
            "mode": mode,
            "score": score,
            "knowledge_id": knowledge_id,
            "why_hit": explanation,
        }
        return row

    @staticmethod
    def _ranked_case_hits(
        hits: list[Mapping[str, Any]],
        *,
        lookup: Mapping[str, Mapping[str, Any]],
        mode: str,
        resolver: Callable[[Mapping[str, Any]], dict[str, Any] | None],
    ) -> list[dict[str, Any]]:
        best_by_case: dict[
            str,
            tuple[tuple[int, int, float, str], Mapping[str, Any], dict[str, Any]],
        ] = {}
        for hit in hits:
            business_case_id = str(hit.get("business_case_id") or "").strip()
            case = lookup.get(business_case_id)
            if case is None:
                case = resolver(hit)
            if case is None:
                continue
            case_id = str(case.get("case_id") or business_case_id).strip()
            if not case_id:
                continue
            variant = hit.get("_query_variant")
            variant = variant if isinstance(variant, Mapping) else {}
            try:
                score = float(
                    hit.get("score")
                    if hit.get("score") is not None
                    else hit.get("match_score") or 0
                )
            except (TypeError, ValueError):
                score = 0.0
            order = (
                int(variant.get("priority_rank") or 0),
                int(variant.get("expansion_cost") or 0),
                -score,
                case_id,
            )
            previous = best_by_case.get(case_id)
            if previous is None or order < previous[0]:
                best_by_case[case_id] = (order, hit, dict(case))

        ranked: list[dict[str, Any]] = []
        for _order, hit, case in sorted(
            best_by_case.values(), key=lambda item: item[0]
        ):
            variant = hit.get("_query_variant")
            formal_why = {
                "status": "FORMAL_PROJECTION_MATCH",
                "claim_safe": True,
                "reasons": list(hit.get("match_reasons") or []),
            }
            why_hit = (
                hit.get("why_hit")
                if isinstance(hit.get("why_hit"), Mapping)
                else formal_why if mode == "SQLITE_FORMAL" else None
            )
            ranked.append(
                HardwareCaseAIRetrievalService._attach_retrieval(
                    case,
                    mode=mode,
                    score=(
                        hit.get("score")
                        if hit.get("score") is not None
                        else hit.get("match_score")
                    ),
                    why_hit=why_hit,
                    knowledge_id=str(hit.get("knowledge_id") or "") or None,
                    query_variant=(
                        variant if isinstance(variant, Mapping) else None
                    ),
                )
            )
        return ranked

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

        # Business case identity is not an arbitrary full-text term. Resolve
        # against the read-only published view before searching.
        direct_id = raw_query.strip().upper()
        if re.fullmatch(r"[A-Z]\d{4,10}", direct_id):
            visible = self._visible_payload(
                role=role, statuses=statuses, historical=historical,
            )
            found = self._case_lookup(visible["results"]).get(direct_id)
            if found is None and self.consumption_service is not None:
                projection = self.consumption_service.search(
                    "", business_case_id=direct_id, limit=2,
                )
                rows = projection.get("results", [])
                if len(rows) == 1:
                    found = self._projection_to_case(rows[0])
            if found is not None and found.get("case_status") == "PUBLISHED":
                case = self._attach_retrieval(
                    found,
                    mode="CASE_ID_EXACT",
                    score=None,
                    why_hit={
                        "status": "EXACT_BUSINESS_CASE_ID",
                        "claim_safe": True,
                        "reasons": [{
                            "matched_field": "business_case_id",
                            "matched_text": direct_id,
                        }],
                    },
                )
                return {
                    **visible,
                    "results": [case],
                    "retrieval": {
                        "mode": "CASE_ID_EXACT",
                        "query_understanding": understood,
                        "degraded": False,
                        "errors": [],
                    },
                }

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
        search_queries = list(understood.get("search_queries") or []) or [
            {"text": str(understood["retrieval_text"]), "kind": "LITERAL", "rules": []}
        ]

        if self.retrieval_query_service is not None:
            try:
                hits: list[dict[str, Any]] = []
                for variant in search_queries:
                    payload = self.retrieval_query_service.search(
                        str(variant["text"]),
                        limit=100,
                    )
                    variant_hits = payload.get("results")
                    if not isinstance(variant_hits, list):
                        raise HardwareCaseAIRetrievalError(
                            "RETRIEVAL_QUERY_RESPONSE_INVALID"
                        )
                    for hit in variant_hits:
                        if isinstance(hit, Mapping):
                            hits.append({**dict(hit), "_query_variant": variant})
                mapped = self._ranked_case_hits(
                    hits,
                    lookup=lookup,
                    mode="OPENSEARCH",
                    resolver=self._resolve_formal_case,
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
                hits = []
                for variant in search_queries:
                    projection_payload = self.consumption_service.search(
                        str(variant["text"]),
                        limit=100,
                    )
                    variant_hits = projection_payload.get("results")
                    if not isinstance(variant_hits, list):
                        raise HardwareCaseAIRetrievalError(
                            "CONSUMPTION_SEARCH_RESPONSE_INVALID"
                        )
                    for hit in variant_hits:
                        if isinstance(hit, Mapping):
                            hits.append({**dict(hit), "_query_variant": variant})
                mapped = self._ranked_case_hits(
                    hits,
                    lookup=lookup,
                    mode="SQLITE_FORMAL",
                    resolver=self._resolve_formal_case,
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

        legacy_rows: list[dict[str, Any]] = []
        for variant in search_queries:
            legacy = self.case_service.search_cases(
                str(variant["text"]),
                role=role,
                statuses=statuses,
                historical=historical,
            )
            if not isinstance(legacy, Mapping) or not isinstance(
                legacy.get("results"), list
            ):
                raise HardwareCaseAIRetrievalError(
                    "HARDWARE_CASE_SEARCH_RESPONSE_INVALID"
                )
            for case in legacy["results"]:
                if not isinstance(case, Mapping):
                    continue
                legacy_rows.append(
                    self._attach_retrieval(
                        case,
                        mode="LEGACY_NORMALIZED",
                        score=None,
                        why_hit={"status": "LEGACY_QUERY_MATCH", "claim_safe": True},
                        query_variant=variant,
                    )
                )
        deduped_legacy: list[dict[str, Any]] = []
        legacy_seen: set[str] = set()
        for case in legacy_rows:
            case_id = str(case.get("case_id") or case.get("business_case_id") or "")
            if case_id in legacy_seen:
                continue
            legacy_seen.add(case_id)
            deduped_legacy.append(case)
        legacy = dict(visible)
        legacy["results"] = deduped_legacy
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
