"""Optional, read-only online query understanding through the existing Unified Runtime."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime
from services.hardware_retrieval_tagger_runtime import package_root

AGENT_ID = "hardware_retrieval.query_understand"
CONFIG_PATH = "config/runtime/agents/hardware_retrieval.query_understand.yaml"

QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "queries", "confidence"],
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["DESIGN_REUSE", "CIRCUIT_RISK", "FIELD_PROBLEM", "TEST_VALIDATION", "GENERAL"],
        },
        "queries": {
            "type": "array", "minItems": 1, "maxItems": 6,
            "items": {"type": "string", "minLength": 1, "maxLength": 90},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": False,
}


class QueryAgentError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class HardwareQueryAgent:
    def __init__(self, *, root: str | Path | None = None, environ: Mapping[str, str] | None = None):
        env = os.environ if environ is None else environ
        base = Path(root).resolve() if root is not None else package_root()
        model = str(env.get("HARDWARE_CASE_MODEL_CONFIG") or "config/runtime/model.local.yaml")
        model_path = Path(model)
        if not model_path.is_absolute():
            model_path = base / model_path
        if not model_path.is_file():
            raise QueryAgentError("MODEL_LOCAL_CONFIG_REQUIRED")
        config_path = base / CONFIG_PATH
        if not config_path.is_file():
            raise QueryAgentError("QUERY_AGENT_CONFIG_MISSING")
        db_path = Path(str(env.get("HARDWARE_QUERY_RUNTIME_DB") or "data/runtime/hardware_query_runtime.db"))
        if not db_path.is_absolute():
            db_path = base / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        loader = AgentConfigLoader(root=base, model_profiles=model_path, schemas={"HardwareQueryUnderstanding": QUERY_SCHEMA}, environ=env)
        self.runtime = ConfiguredAgentRuntime(SqliteTaskStore(db_path), config_loader=loader)
        self.resolved = self.runtime.load_agent(config_path)
        if self.resolved.definition.agent_id != AGENT_ID:
            raise QueryAgentError("QUERY_AGENT_ID_MISMATCH")

    def understand(self, query: str) -> dict[str, Any]:
        payload = {"query": str(query)[:1024], "read_only": True}
        request_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        result = self.runtime.invoke(AgentRequest(
            request_id="hardware-query:" + request_hash,
            agent_id=AGENT_ID,
            input=payload,
            metadata={"business_domain": "HARDWARE_CASE", "pipeline_stage": "QUERY_UNDERSTANDING", "formal_knowledge_write": False},
        ))
        if result.status != RuntimeStatus.COMPLETED:
            error = getattr(result, "error", None)
            raise QueryAgentError(str(getattr(error, "code", None) or "QUERY_AGENT_EXECUTION_FAILED"))
        data = result.data
        if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
            raise QueryAgentError("QUERY_AGENT_OUTPUT_INVALID")
        queries = list(dict.fromkeys(str(q).strip() for q in data["queries"] if isinstance(q, str) and q.strip()))
        if not 1 <= len(queries) <= 6 or any(len(q) > 90 for q in queries):
            raise QueryAgentError("QUERY_AGENT_OUTPUT_INVALID")
        return {
            "intent": str(data["intent"]), "queries": queries,
            "confidence": float(data["confidence"]),
            "trace": {"agent_id": result.agent_id, "task_id": result.task_id,
                      "run_id": result.run_id,
                      "provider_calls": int(getattr(result.execution, "provider_calls", 0) or 0),
                      "agent_config_hash": self.resolved.config_hash},
        }
