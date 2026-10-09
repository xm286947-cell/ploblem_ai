"""Opt-in read-only Query/Consumption Agents on Unified Runtime; no secret logging."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime

QUERY_AGENT = "hardware_retrieval.query_understand"
CONSUME_AGENT = "hardware_knowledge.engineering_consume"
_FIELDS = ("title", "symptom", "root_cause", "failure_mechanism", "actions",
           "verification_result", "engineering_rule", "design_constraint",
           "verification_method", "applicability", "interface", "signal",
           "device_refs", "key_parameters", "failure_mode", "diagnostic_clue",
           "conclusion", "occurrence_condition", "analysis_process")
QUERY_SCHEMA = {"type": "object", "required": ["intent", "queries"],
                "properties": {"intent": {"type": "string", "enum": [
                    "DESIGN_REUSE", "COMPONENT_CIRCUIT_RISK", "FIELD_PROBLEM", "TEST_VALIDATION", "GENERAL"]},
                    "queries": {"type": "array", "minItems": 1, "maxItems": 5,
                                "items": {"type": "string", "minLength": 1, "maxLength": 80}}},
                "additionalProperties": False}
CONSUME_SCHEMA = {"type": "object", "required": ["summary", "checks"],
                  "properties": {"summary": {"type": "string", "maxLength": 500},
                      "checks": {"type": "array", "maxItems": 10, "items": {
                          "type": "object",
                          "required": ["advice", "knowledge_id", "field", "evidence_id"],
                          "properties": {
                              "advice": {"type": "string", "minLength": 1, "maxLength": 400},
                              "knowledge_id": {"type": "string", "minLength": 1},
                              "field": {"type": "string", "enum": list(_FIELDS)},
                              "evidence_id": {"type": "string", "minLength": 1}},
                          "additionalProperties": False}}},
                  "additionalProperties": False}

class HardwareR2Agent:
    """Provider can only run after explicit non-production enablement."""
    def __init__(self, kind: str, *, root: str | Path | None = None,
                 environ: Mapping[str, str] | None = None):
        if kind not in ("query", "consume"):
            raise ValueError("AGENT_KIND_INVALID")
        self.kind = kind
        self.root = Path(root).resolve() if root else Path(__file__).resolve().parents[1]
        self.env = environ if environ is not None else os.environ
        self.agent_id = QUERY_AGENT if kind == "query" else CONSUME_AGENT
        self.enabled = self.env.get("HARDWARE_R2_REAL_PROVIDER_ENABLED") == "1"
        self.runtime = None

    def _prepare(self):
        if self.runtime is not None:
            return
        model = Path(self.env.get("HARDWARE_CASE_MODEL_CONFIG") or "config/runtime/model.local.yaml").expanduser()
        if not model.is_absolute():
            model = self.root / model
        if not model.is_file():
            raise RuntimeError("MODEL_CONFIG_MISSING")
        agent_config = self.root / ("config/runtime/agents/hardware_r2.query.yaml"
                                   if self.kind == "query" else "config/runtime/agents/hardware_r2.consume.yaml")
        db = Path(self.env.get("HARDWARE_R2_RUNTIME_DB") or "data/runtime/hardware_r2.db").expanduser()
        if not db.is_absolute():
            db = self.root / db
        db.parent.mkdir(parents=True, exist_ok=True)
        loader = AgentConfigLoader(
            root=self.root, model_profiles=model,
            schemas={"HardwareR2QueryPlan": QUERY_SCHEMA, "HardwareR2Advice": CONSUME_SCHEMA},
            environ=self.env)
        runtime = ConfiguredAgentRuntime(SqliteTaskStore(db), config_loader=loader)
        resolved = runtime.load_agent(agent_config)
        if resolved.definition.agent_id != self.agent_id:
            raise RuntimeError("AGENT_ID_MISMATCH")
        self._configured_model = resolved.provider.model
        self._configured_provider = resolved.provider.type
        self.runtime = runtime

    def invoke(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "DISABLED", "data": None, "trace": None, "reason": "NOT_ENABLED"}
        try:
            self._prepare()
            raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, default=str)
            request_id = "hw-r2-" + self.kind + ":" + hashlib.sha256(raw.encode()).hexdigest()[:24]
            result = self.runtime.invoke(AgentRequest(
                request_id=request_id, agent_id=self.agent_id, input=dict(payload),
                metadata={"business_domain": "HARDWARE_CASE", "formal_knowledge_write": False}))
            ex = result.execution
            trace = {"task_id": result.task_id, "run_id": result.run_id,
                     "agent_id": self.agent_id, "trace_id": ex.trace_id,
                     "provider": ex.provider, "model": ex.model,
                     "configured_provider": self._configured_provider,
                     "configured_model": self._configured_model,
                     "provider_calls": ex.provider_calls, "token_usage": ex.token_usage,
                     "duration_ms": ex.duration_ms}
            if result.status != RuntimeStatus.COMPLETED or not isinstance(result.data, dict):
                return {"status": "BLOCKED", "data": None, "trace": trace,
                        "reason": str(getattr(result.error, "code", None) or "RUNTIME_NOT_COMPLETED")}
            return {"status": "COMPLETED", "data": result.data, "trace": trace, "reason": None}
        except Exception as err:
            return {"status": "BLOCKED", "data": None, "trace": None,
                    "reason": type(err).__name__}

def validate_advice(data: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """Mechanical source validation. Human review still needed for semantic support."""
    evidence = {str(x) for x in row.get("evidence_refs") or []}
    checks = data.get("checks")
    if not isinstance(checks, list):
        raise ValueError("ADVICE_SCHEMA_INVALID")
    out = []
    for entry in checks:
        if not isinstance(entry, Mapping):
            raise ValueError("ADVICE_SCHEMA_INVALID")
        field = str(entry.get("field") or "")
        if (str(entry.get("knowledge_id") or "") != str(row.get("knowledge_id") or "")
            or field not in _FIELDS or not row.get(field)
            or str(entry.get("evidence_id") or "") not in evidence
            or not str(entry.get("advice") or "").strip()):
            raise ValueError("ADVICE_SOURCE_REF_INVALID")
        out.append(dict(entry))
    return {"summary": str(data.get("summary") or ""), "checks": out}
