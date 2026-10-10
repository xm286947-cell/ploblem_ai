"""Read-only, evidence-grounded Agent consumption of existing Formal knowledge."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime
from services.hardware_retrieval_tagger_runtime import package_root

ANALYSIS_AGENT_ID = "hardware_retrieval.engineering_consumption"
ANALYSIS_CONFIG = "config/runtime/agents/hardware_retrieval.engineering_consumption.yaml"
TASK_INTENTS = {"DESIGN_REUSE", "CIRCUIT_RISK", "FIELD_PROBLEM", "TEST_VALIDATION"}
SOURCE_FIELDS = (
    "title", "symptom", "root_cause", "failure_mechanism", "actions",
    "verification_result", "engineering_rule", "design_constraint",
    "verification_method", "applicability", "failure_mode", "diagnostic_clue",
    "conclusion", "occurrence_condition", "interface", "signal",
)
ANALYSIS_SCHEMA = {
    "type": "object", "required": ["task_intent", "items"],
    "properties": {
        "task_intent": {"type": "string", "enum": sorted(TASK_INTENTS)},
        "items": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
            "type": "object", "required": ["suggestion", "source_field", "source_quote"],
            "properties": {
                "suggestion": {"type": "string", "minLength": 1, "maxLength": 600},
                "source_field": {"type": "string", "enum": list(SOURCE_FIELDS)},
                "source_quote": {"type": "string", "minLength": 3, "maxLength": 800},
            }, "additionalProperties": False}},
    }, "additionalProperties": False,
}


class EngineeringAnalysisError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class EngineeringConsumptionRuntime:
    def __init__(self, *, root: str | Path | None = None, environ: Mapping[str, str] | None = None):
        env = os.environ if environ is None else environ
        base = Path(root).resolve() if root is not None else package_root()
        model_path = Path(str(env.get("HARDWARE_CASE_MODEL_CONFIG") or "config/runtime/model.local.yaml"))
        if not model_path.is_absolute():
            model_path = base / model_path
        if not model_path.is_file():
            raise EngineeringAnalysisError("MODEL_LOCAL_CONFIG_REQUIRED")
        config_path = base / ANALYSIS_CONFIG
        if not config_path.is_file():
            raise EngineeringAnalysisError("ANALYSIS_AGENT_CONFIG_MISSING")
        db = Path(str(env.get("HARDWARE_CONSUMPTION_RUNTIME_DB") or "data/runtime/hardware_consumption_runtime.db"))
        if not db.is_absolute():
            db = base / db
        db.parent.mkdir(parents=True, exist_ok=True)
        loader = AgentConfigLoader(root=base, model_profiles=model_path, schemas={"HardwareEngineeringAnalysis": ANALYSIS_SCHEMA}, environ=env)
        self.runtime = ConfiguredAgentRuntime(SqliteTaskStore(db), config_loader=loader)
        self.resolved = self.runtime.load_agent(config_path)
        if self.resolved.definition.agent_id != ANALYSIS_AGENT_ID:
            raise EngineeringAnalysisError("ANALYSIS_AGENT_ID_MISMATCH")

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        identity = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        result = self.runtime.invoke(AgentRequest(
            request_id="hardware-engineering-consumption:" + identity,
            agent_id=ANALYSIS_AGENT_ID, input=payload,
            metadata={"business_domain": "HARDWARE_CASE", "pipeline_stage": "ENGINEERING_CONSUMPTION", "formal_knowledge_write": False},
        ))
        if result.status != RuntimeStatus.COMPLETED:
            raise EngineeringAnalysisError(str(getattr(getattr(result, "error", None), "code", None) or "ANALYSIS_AGENT_FAILED"))
        if not isinstance(result.data, dict):
            raise EngineeringAnalysisError("ANALYSIS_OUTPUT_INVALID")
        return {**result.data, "_trace": {"agent_id": result.agent_id, "task_id": result.task_id,
                                         "run_id": result.run_id,
                                         "provider_calls": int(getattr(result.execution, "provider_calls", 0) or 0),
                                         "provider": getattr(result.execution, "provider", None),
                                         "model": getattr(result.execution, "model", None),
                                         "duration_ms": getattr(result.execution, "duration_ms", None),
                                         "token_usage": dict(getattr(result.execution, "token_usage", {}) or {}),
                                         "trace_id": getattr(result.execution, "trace_id", None)}}


class HardwareEngineeringConsumption:
    def __init__(self, consumption: Any, *, agent: Any | None = None):
        self.consumption = consumption
        self.agent = agent

    def analyze(self, knowledge_id: str, task_intent: str) -> dict[str, Any]:
        if task_intent not in TASK_INTENTS:
            raise EngineeringAnalysisError("TASK_INTENT_UNSUPPORTED")
        formal = self.consumption.get(knowledge_id)
        if not isinstance(formal, Mapping):
            raise EngineeringAnalysisError("FORMAL_KNOWLEDGE_NOT_FOUND")
        facts = {name: formal[name] for name in SOURCE_FIELDS if isinstance(formal.get(name), str) and formal[name].strip()}
        if not facts:
            raise EngineeringAnalysisError("NO_GROUNDED_KNOWLEDGE_FIELDS")
        agent = self.agent
        if agent is None:
            if os.getenv("HARDWARE_CONSUMPTION_AGENT_ENABLED") != "1":
                raise EngineeringAnalysisError("CONSUMPTION_AGENT_DISABLED")
            agent = EngineeringConsumptionRuntime()
        output = agent.analyze({"task_intent": task_intent, "knowledge_id": knowledge_id,
                                "facts": facts, "read_only": True})
        if not isinstance(output, Mapping) or output.get("task_intent") != task_intent:
            raise EngineeringAnalysisError("ANALYSIS_TASK_MISMATCH")
        entries = output.get("items")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 6:
            raise EngineeringAnalysisError("ANALYSIS_ITEMS_INVALID")
        validated = []
        for row in entries:
            if not isinstance(row, Mapping):
                raise EngineeringAnalysisError("ANALYSIS_ITEMS_INVALID")
            field = row.get("source_field")
            quote = row.get("source_quote")
            suggestion = row.get("suggestion")
            if field not in facts or not isinstance(quote, str) or len(quote) < 3 or quote not in facts[field]:
                raise EngineeringAnalysisError("ANALYSIS_UNGROUNDED_QUOTE")
            if not isinstance(suggestion, str) or not suggestion.strip() or len(suggestion) > 600:
                raise EngineeringAnalysisError("ANALYSIS_SUGGESTION_INVALID")
            # A valid citation alone cannot prove a free-form model claim.
            # For the first safe MVP surface only the verbatim Formal excerpt;
            # task selection is agent-driven, but new engineering claims are not.
            validated.append({"suggestion": quote, "knowledge_id": formal["knowledge_id"],
                              "business_case_id": formal.get("business_case_id"),
                              "source_field": field, "source_quote": quote,
                              "advice_scope": "VERBATIM_FORMAL_EXCERPT"})
        return {"status": "AI_ADVISORY_REQUIRES_ENGINEERING_REVIEW", "task_intent": task_intent,
                "knowledge_id": formal["knowledge_id"], "business_case_id": formal.get("business_case_id"),
                "items": validated, "case_evidence_refs": list(formal.get("evidence_refs") or []),
                "evidence_binding": "CASE_LEVEL_ONLY", "trace": output.get("_trace")}
