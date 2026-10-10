"""Read-only R2 engineering query bridge shared by both hardware consumer pages.

The model may propose search terms, but Formal Knowledge is the sole source of
cases, facts and evidence. Existing deterministic Consumption v1 is unchanged.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from services.hardware_knowledge_consumption import normalize_search_text

QUERY_AGENT_ID = "hardware_retrieval.query_understand"
QUERY_SCHEMA = {
    "type": "object",
    "required": ["intent", "normalized_query", "query_terms"],
    "properties": {
        "intent": {"type": "string", "enum": [
            "DESIGN_REUSE", "RISK", "FIELD_PROBLEM", "TEST_VALIDATION", "GENERAL"
        ]},
        "normalized_query": {"type": "string", "minLength": 1},
        "query_terms": {
            "type": "array", "maxItems": 8,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": False,
}
_TERMS = ("模拟量", "adc", "参考源", "串口", "乱码", "mcu", "单片机", "复位", "供电", "电源")
_BUSINESS_ID = re.compile(r"^A\d{4,}$", re.I)


def _query_terms(value: str) -> list[str]:
    text = normalize_search_text(value)
    terms = [item for item in _TERMS if item in text]
    if "单片机" in terms:
        terms = ["mcu" if item == "单片机" else item for item in terms]
    if not terms and len(text) <= 40 and not re.search(r"[？?。！!，,]", text):
        return [text] if text else []
    return list(dict.fromkeys(terms))[:8]


def _is_natural(text: str) -> bool:
    return len(text.strip()) >= 17 or any(
        marker in text for marker in ("如何", "怎么", "经验", "建议", "请帮我", "？", "?")
    )


class HardwareR2RuntimeQueryAgent:
    """Lazy configured Unified Runtime adapter; no secret contents are exposed."""

    def __init__(self, *, root: Path | None = None):
        self.root = root or Path(__file__).resolve().parents[1]

    def __call__(self, text: str) -> tuple[dict[str, Any], dict[str, Any]]:
        from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
        from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime

        model_path = Path(os.environ.get("HARDWARE_CASE_MODEL_CONFIG") or "config/runtime/model.local.yaml")
        if not model_path.is_absolute():
            model_path = self.root / model_path
        if not model_path.is_file():
            raise RuntimeError("MODEL_LOCAL_CONFIG_REQUIRED")
        db_path = Path(os.environ.get("HARDWARE_R2_RUNTIME_DB") or "data/runtime/hardware_r2_query.db")
        if not db_path.is_absolute():
            db_path = self.root / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        loader = AgentConfigLoader(
            root=self.root,
            model_profiles=model_path,
            schemas={"HardwareQueryUnderstandingOutput": QUERY_SCHEMA},
            environ=os.environ,
        )
        runtime = ConfiguredAgentRuntime(SqliteTaskStore(db_path), config_loader=loader)
        agent_config = self.root / "config/runtime/agents/hardware_retrieval.query_understand.yaml"
        resolved = runtime.load_agent(agent_config)
        if resolved.definition.agent_id != QUERY_AGENT_ID:
            raise RuntimeError("QUERY_AGENT_ID_MISMATCH")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
        result = runtime.invoke(AgentRequest(
            request_id="hardware-r2-query:" + digest,
            agent_id=QUERY_AGENT_ID,
            input={
                "user_query": text,
                "task": "Return engineering retrieval intent and query terms only.",
            },
            metadata={
                "business_domain": "HARDWARE_CASE",
                "pipeline_stage": "QUERY_UNDERSTANDING",
                "read_only": True,
            },
        ))
        trace = {
            "agent_id": QUERY_AGENT_ID,
            "task_id": result.task_id,
            "run_id": result.run_id,
            "status": str(result.status),
            "trace_id": result.execution.trace_id,
            "provider": result.execution.provider,
            "model": result.execution.model,
            "provider_calls": result.execution.provider_calls,
            "token_usage": dict(result.execution.token_usage or {}),
            "duration_ms": result.execution.duration_ms,
        }
        if result.status != RuntimeStatus.COMPLETED or not isinstance(result.data, dict):
            raise RuntimeError("QUERY_RUNTIME_NOT_COMPLETED")
        return result.data, trace


class HardwareR2QueryService:
    """Read-only unified recall for Case Search and Formal Knowledge Consumption."""

    def __init__(self, consumption_service: Any, *, agent: Callable | None = None):
        self.consumption = consumption_service
        self.agent = agent

    def search(self, text: str = "", *, limit: int = 100) -> dict[str, Any]:
        query = str(text or "").strip()
        if len(query) > 512:
            raise ValueError("QUERY_TOO_LONG")
        if not 1 <= limit <= 100:
            raise ValueError("SEARCH_LIMIT_INVALID")
        mode = "DETERMINISTIC"
        agent_status = "FAST_PATH" if not _is_natural(query) else "NOT_CONFIGURED"
        trace: dict[str, Any] = {}
        terms = _query_terms(query)
        intent = "GENERAL"
        if query and _is_natural(query) and self.agent is not None:
            try:
                plan, trace = self.agent(query)
                if not isinstance(plan, Mapping):
                    raise ValueError("QUERY_PLAN_INVALID")
                intent = str(plan.get("intent") or "GENERAL")
                if intent not in {
                    "DESIGN_REUSE", "RISK", "FIELD_PROBLEM", "TEST_VALIDATION", "GENERAL"
                }:
                    raise ValueError("QUERY_INTENT_INVALID")
                candidates = plan.get("query_terms")
                if not isinstance(candidates, list):
                    raise ValueError("QUERY_TERMS_INVALID")
                terms = list(dict.fromkeys(
                    str(term).strip() for term in candidates[:8]
                    if isinstance(term, str) and 1 <= len(term.strip()) <= 48
                )) or terms
                agent_status = "COMPLETED"
                mode = "RUNTIME_QUERY_PLAN"
            except Exception as error:
                agent_status = "BLOCKED"
                trace = {
                    "error_code": str(getattr(error, "code", None) or type(error).__name__)
                }

        if _BUSINESS_ID.fullmatch(query):
            rows = self.consumption.search(
                "", business_case_id=query.upper(), limit=limit
            ).get("results") or []
            results = [dict(row) for row in rows]
            for row in results:
                row["match_reasons"] = [{
                    "matched_field": "business_case_id",
                    "matched_text": query.upper(), "weight": 100,
                }]
                row["match_score"] = 100
        elif not query:
            results = list(self.consumption.search("", limit=limit).get("results") or [])
        else:
            exact = list(self.consumption.search(query, limit=limit).get("results") or [])
            if exact:
                results = exact
            else:
                matches: dict[str, dict[str, Any]] = {}
                for term in terms:
                    rows = self.consumption.search(term, limit=limit).get("results") or []
                    for row in rows:
                        key = str(row.get("knowledge_id"))
                        state = matches.setdefault(
                            key, {"row": dict(row), "terms": set(), "reasons": []}
                        )
                        state["terms"].add(term)
                        state["reasons"].extend(row.get("match_reasons") or [])
                results = []
                for state in matches.values():
                    if terms and len(state["terms"]) == len(terms):
                        row = state["row"]
                        row["match_reasons"] = state["reasons"]
                        row["match_score"] = sum(
                            int(item.get("weight") or 0) for item in state["reasons"]
                        )
                        results.append(row)
                results.sort(
                    key=lambda row: (-int(row["match_score"]), str(row.get("knowledge_id")))
                )
        return {
            "contract_version": "hardware-r2-assisted-search/v1",
            "results": results[:limit],
            "query": {"original": query, "terms": terms, "intent": intent},
            "retrieval": {
                "mode": mode, "agent_status": agent_status, "trace": trace,
            },
        }
