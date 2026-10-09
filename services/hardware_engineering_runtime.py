"""Unified Runtime-backed engineering Knowledge consumption (non-production)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime
from services.hardware_query_runtime import HardwareQueryRuntimeError

AGENT_ID = "hardware_retrieval.engineering_consume"
CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["recommendation", "source_field", "source_excerpt", "evidence_id"],
    "properties": {
        "recommendation": {"type": "string", "minLength": 4, "maxLength": 500},
        "source_field": {"type": "string", "minLength": 2},
        "source_excerpt": {"type": "string", "minLength": 4},
        "evidence_id": {"type": "string", "minLength": 2},
    },
    "additionalProperties": False,
}
ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "checks", "unknowns"],
    "properties": {
        "summary": {"type": "string", "maxLength": 500},
        "checks": {"type": "array", "minItems": 1, "maxItems": 8, "items": CHECK_SCHEMA},
        "unknowns": {"type": "array", "maxItems": 10,
                     "items": {"type": "string", "maxLength": 200}},
    },
    "additionalProperties": False,
}


class HardwareEngineeringRuntimeInvoker:
    def __init__(self, *, root: str | Path | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        env = os.environ if environ is None else environ
        root_path = Path(root or Path(__file__).resolve().parents[1]).resolve()

        def path(value):
            p = Path(value).expanduser()
            return (p if p.is_absolute() else root_path / p).resolve()

        model = path(env.get("HARDWARE_CASE_MODEL_CONFIG") or "config/runtime/model.local.yaml")
        agent = path(env.get("HARDWARE_ENGINEERING_AGENT_CONFIG") or
                     "config/runtime/agents/hardware_retrieval.engineering_consume.yaml")
        db = path(env.get("HARDWARE_ENGINEERING_RUNTIME_DB") or
                  "data/runtime/hardware_engineering_runtime.db")
        if not model.is_file():
            raise HardwareQueryRuntimeError("MODEL_LOCAL_CONFIG_REQUIRED")
        if not agent.is_file():
            raise HardwareQueryRuntimeError("ENGINEERING_AGENT_CONFIG_REQUIRED")
        db.parent.mkdir(parents=True, exist_ok=True)
        loader = AgentConfigLoader(root=root_path, model_profiles=model,
                                   schemas={"HardwareEngineeringAnalysis": ANALYSIS_SCHEMA},
                                   environ=env)
        self.store = SqliteTaskStore(db)
        self.runtime = ConfiguredAgentRuntime(self.store, config_loader=loader)
        self.resolved = self.runtime.load_agent(agent)
        if self.resolved.definition.agent_id != AGENT_ID:
            raise HardwareQueryRuntimeError("ENGINEERING_AGENT_ID_MISMATCH")

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.runtime.invoke(AgentRequest(
            request_id="hardware-engineering:" + uuid4().hex,
            agent_id=AGENT_ID, input=payload,
            metadata={"business_domain": "HARDWARE_CASE",
                      "pipeline_stage": "ENGINEERING_CONSUMPTION",
                      "formal_knowledge_write": False},
        ))
        if result.status != RuntimeStatus.COMPLETED or not isinstance(result.data, dict):
            raise HardwareQueryRuntimeError(str(getattr(result.error, "code", None)
                                                or "ENGINEERING_AGENT_NOT_COMPLETED"))
        calls = self.store.count_task_provider_calls(result.task_id)
        if calls < 1:
            raise HardwareQueryRuntimeError("ENGINEERING_PROVIDER_TRACE_MISSING")
        return {"data": result.data, "trace": {
            "agent_id": AGENT_ID,
            "task_id": result.task_id,
            "run_id": result.run_id,
            "provider_calls": calls,
            "provider": result.execution.provider or self.resolved.provider.type,
            "model": result.execution.model or self.resolved.provider.model,
            "duration_ms": result.execution.duration_ms,
            "token_usage": dict(result.execution.token_usage),
            "trace_id": result.execution.trace_id,
        }}
