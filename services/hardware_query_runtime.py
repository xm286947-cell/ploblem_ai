"""Optional online query-understanding Agent using existing unified Runtime.

Only retrieval guidance is returned; no Formal Knowledge writes or case facts.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime

AGENT_ID = "hardware_retrieval.query_understand"
AGENT_CONFIG = "config/runtime/agents/hardware_retrieval.query_understand.yaml"
MODEL_CONFIG = "config/runtime/model.local.yaml"

QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "search_terms"],
    "properties": {
        "intent": {"type": "string", "enum": [
            "DESIGN_REUSE", "RISK", "FIELD_PROBLEM", "TEST_VALIDATION", "GENERAL",
        ]},
        "search_terms": {"type": "array", "minItems": 1, "maxItems": 5,
                         "items": {"type": "string", "minLength": 2, "maxLength": 80}},
    },
    "additionalProperties": False,
}


class HardwareQueryRuntimeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class HardwareQueryRuntimeInvoker:
    def __init__(self, *, root: str | Path | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        env = os.environ if environ is None else environ
        root_path = Path(root or Path(__file__).resolve().parents[1]).resolve()

        def resolve(path: str) -> Path:
            p = Path(path).expanduser()
            return (p if p.is_absolute() else root_path / p).resolve()

        model_path = resolve(str(env.get("HARDWARE_CASE_MODEL_CONFIG") or MODEL_CONFIG))
        agent_path = resolve(str(env.get("HARDWARE_QUERY_AGENT_CONFIG") or AGENT_CONFIG))
        db_path = resolve(str(env.get("HARDWARE_QUERY_RUNTIME_DB") or
                              "data/runtime/hardware_query_runtime.db"))
        if not agent_path.is_file():
            raise HardwareQueryRuntimeError("QUERY_AGENT_CONFIG_REQUIRED")
        if not model_path.is_file():
            raise HardwareQueryRuntimeError("MODEL_LOCAL_CONFIG_REQUIRED")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        loader = AgentConfigLoader(
            root=root_path,
            model_profiles=model_path,
            schemas={"HardwareQueryUnderstanding": QUERY_SCHEMA},
            environ=env,
        )
        self.store = SqliteTaskStore(db_path)
        self.runtime = ConfiguredAgentRuntime(self.store, config_loader=loader)
        resolved = self.runtime.load_agent(agent_path)
        if resolved.definition.agent_id != AGENT_ID:
            raise HardwareQueryRuntimeError("QUERY_AGENT_ID_MISMATCH")

    def __call__(self, query: str) -> dict[str, Any]:
        result = self.runtime.invoke(AgentRequest(
            request_id="hardware-query:" + uuid4().hex,
            agent_id=AGENT_ID,
            input={"query": query},
            metadata={"business_domain": "HARDWARE_CASE",
                      "pipeline_stage": "ONLINE_QUERY_UNDERSTANDING",
                      "formal_knowledge_write": False},
        ))
        if result.status != RuntimeStatus.COMPLETED:
            error = getattr(result, "error", None)
            raise HardwareQueryRuntimeError(
                str(getattr(error, "code", None) or "QUERY_AGENT_NOT_COMPLETED")
            )
        if not isinstance(result.data, dict):
            raise HardwareQueryRuntimeError("QUERY_AGENT_OUTPUT_INVALID")
        provider_calls = self.store.count_task_provider_calls(result.task_id)
        if provider_calls < 1:
            raise HardwareQueryRuntimeError("QUERY_PROVIDER_TRACE_MISSING")
        trace = {
            "agent_id": AGENT_ID,
            "task_id": result.task_id,
            "run_id": result.run_id,
            "provider_calls": provider_calls,
            "provider": result.execution.provider,
            "model": result.execution.model,
            "duration_ms": result.execution.duration_ms,
            "token_usage": dict(result.execution.token_usage),
            "trace_id": result.execution.trace_id,
            "status": "COMPLETED",
        }
        return {"intent": result.data["intent"],
                "search_terms": result.data["search_terms"], "trace": trace}
