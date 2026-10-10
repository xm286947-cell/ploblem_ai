"""Additive read-only search over authorized Formal projections.

Case search owns query understanding and OpenSearch/SQLite/Legacy fallbacks.
This adapter only resolves already-returned published cases back to Formal
projection identities; it never synthesizes source facts or modifies v1 search.
"""
from __future__ import annotations
from typing import Any


class HardwareAssistedSearch:
    def __init__(self, case_search: Any, consumption: Any):
        self.case_search = case_search
        self.consumption = consumption

    def search(self, text: str = "", *, interface: str | None = None,
               signal: str | None = None, device: str | None = None,
               limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise ValueError("SEARCH_LIMIT_INVALID")
        filters = {"interface": interface, "signal": signal, "device": device}
        # Existing Public v1 still owns exact filters; no widening allowed.
        filtered = self.consumption.search("", limit=500, **filters)
        allowed = {str(row["knowledge_id"]): row for row in filtered["results"]}
        if not text.strip():
            return {"contract_version": "hardware-query/v1", "results": list(allowed.values())[:limit],
                    "retrieval": {"mode": "FORMAL_LIST", "query_agent": {"status": "FAST_PATH", "trace": None}}}
        payload = self.case_search.search_cases(text)
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for case in payload.get("results", []):
            retrieval = case.get("retrieval") or {}
            case_id = str(case.get("business_case_id") or case.get("case_id") or "")
            knowledge_id = str(case.get("knowledge_id") or retrieval.get("knowledge_id") or "")
            if knowledge_id and knowledge_id in allowed:
                row = allowed[knowledge_id]
            else:
                linked = self.consumption.search("", business_case_id=case_id, limit=2)
                rows = linked.get("results") or []
                if len(rows) != 1 or str(rows[0].get("knowledge_id")) not in allowed:
                    continue
                row = allowed[str(rows[0]["knowledge_id"])]
            key = str(row["knowledge_id"])
            if key in seen:
                continue
            seen.add(key)
            why = retrieval.get("why_hit") or {}
            score = retrieval.get("score")
            results.append({**row, "match_score": score if score is not None else 0,
                            "match_reasons": why.get("reasons") or [],
                            "retrieval_reason": why.get("status") or "CASE_SEARCH_LINK"})
            if len(results) >= limit:
                break
        return {"contract_version": "hardware-query/v1", "results": results,
                "retrieval": payload.get("retrieval") or {}}
