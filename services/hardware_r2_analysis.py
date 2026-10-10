"""Read-only evidence-constrained engineering reuse of Formal Knowledge.

The model selects existing source fields; user-visible engineering text is
emitted VERBATIM from the Formal projection. This prevents fabricated advice.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Mapping

ANALYSIS_AGENT_ID = "hardware_retrieval.engineering_consumption"
_ALLOWED = {
    "DESIGN_REUSE": (
        "engineering_rule", "design_constraint", "verification_method",
        "applicability", "failure_mechanism",
    ),
    "RISK": (
        "failure_mechanism", "root_cause", "design_constraint",
        "diagnostic_clue", "key_parameters",
    ),
    "FIELD_PROBLEM": (
        "symptom", "occurrence_condition", "root_cause",
        "actions", "verification_result",
    ),
    "TEST_VALIDATION": (
        "verification_method", "verification_result",
        "design_constraint", "failure_mechanism",
    ),
}
_LABELS = {
    "engineering_rule": "工程规则", "design_constraint": "设计约束",
    "verification_method": "验证方法", "applicability": "适用范围",
    "failure_mechanism": "失效机理", "root_cause": "问题根因",
    "diagnostic_clue": "诊断线索", "key_parameters": "关键参数",
    "symptom": "问题现象", "occurrence_condition": "发生条件",
    "actions": "解决措施", "verification_result": "验证结果",
}
_SCHEMA = {
    "type": "object",
    "required": ["selected_fields", "unknowns"],
    "properties": {
        "selected_fields": {
            "type": "array", "maxItems": 4, "uniqueItems": True,
            "items": {"type": "string", "enum": sorted(_LABELS)},
        },
        "unknowns": {
            "type": "array", "maxItems": 4,
            "items": {"type": "string", "maxLength": 120},
        },
    },
    "additionalProperties": False,
}


class HardwareR2EngineeringAgent:
    """Unified Runtime adapter with a single capped Provider step."""

    def __init__(self, *, root: Path | None = None):
        self.root = root or Path(__file__).resolve().parents[1]

    def __call__(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
        from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime

        model_file = Path(
            os.environ.get("HARDWARE_CASE_MODEL_CONFIG")
            or "config/runtime/model.local.yaml"
        )
        if not model_file.is_absolute():
            model_file = self.root / model_file
        if not model_file.is_file():
            raise RuntimeError("MODEL_LOCAL_CONFIG_REQUIRED")
        db_path = Path(
            os.environ.get("HARDWARE_R2_RUNTIME_DB")
            or "data/runtime/hardware_r2_consumption.db"
        )
        if not db_path.is_absolute():
            db_path = self.root / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        loader = AgentConfigLoader(
            root=self.root, model_profiles=model_file,
            schemas={"HardwareEngineeringConsumptionOutput": _SCHEMA},
            environ=os.environ,
        )
        runtime = ConfiguredAgentRuntime(SqliteTaskStore(db_path), config_loader=loader)
        resolved = runtime.load_agent(
            self.root / "config/runtime/agents/hardware_retrieval.engineering_consumption.yaml"
        )
        if resolved.definition.agent_id != ANALYSIS_AGENT_ID:
            raise RuntimeError("ENGINEERING_AGENT_ID_MISMATCH")
        digest = hashlib.sha256(repr(sorted(payload.items())).encode("utf-8")).hexdigest()[:24]
        result = runtime.invoke(AgentRequest(
            request_id="hardware-r2-engineering:" + digest,
            agent_id=ANALYSIS_AGENT_ID,
            input=payload,
            metadata={
                "business_domain": "HARDWARE_CASE",
                "pipeline_stage": "KNOWLEDGE_CONSUMPTION",
                "read_only": True,
            },
        ))
        trace = {
            "agent_id": ANALYSIS_AGENT_ID, "task_id": result.task_id,
            "run_id": result.run_id, "trace_id": result.execution.trace_id,
            "provider": result.execution.provider,
            "model": result.execution.model,
            "provider_calls": result.execution.provider_calls,
            "token_usage": dict(result.execution.token_usage or {}),
            "duration_ms": result.execution.duration_ms,
        }
        if result.status != RuntimeStatus.COMPLETED or not isinstance(result.data, dict):
            raise RuntimeError("ENGINEERING_AGENT_NOT_COMPLETED")
        return result.data, trace


class HardwareR2EngineeringConsumptionService:
    def __init__(self, consumption_service: Any, *, agent: Callable | None = None):
        self.consumption = consumption_service
        self.agent = agent

    def analyze(self, *, knowledge_id: str, intent: str) -> dict[str, Any]:
        if intent not in _ALLOWED:
            raise ValueError("ENGINEERING_INTENT_INVALID")
        row = self.consumption.get(knowledge_id)
        if not isinstance(row, Mapping):
            raise LookupError("KNOWLEDGE_NOT_FOUND")
        available = {
            field: row[field] for field in _ALLOWED[intent]
            if row.get(field) not in (None, "", [], {})
        }
        if self.agent is None:
            return {
                "status": "BLOCKED", "code": "ENGINEERING_AGENT_NOT_CONFIGURED",
                "knowledge_id": knowledge_id, "recommendations": [], "trace": {},
            }
        try:
            plan, trace = self.agent({
                "knowledge_id": knowledge_id,
                "business_case_id": str(row.get("business_case_id") or ""),
                "intent": intent,
                "available_fields": available,
            })
            if not isinstance(plan, Mapping) or not isinstance(plan.get("selected_fields"), list):
                raise ValueError("ENGINEERING_PLAN_INVALID")
            fields = plan["selected_fields"]
            if len(fields) > 4 or len(fields) != len(set(map(str, fields))):
                raise ValueError("ENGINEERING_FIELD_LIST_INVALID")
            if any(not isinstance(field, str) or field not in available for field in fields):
                raise ValueError("ENGINEERING_SOURCE_FIELD_INVALID")
            references = [
                value for value in row.get("evidence_refs", []) if isinstance(value, str)
            ]
            results = [
                {
                    "label": _LABELS[field],
                    "text": available[field],
                    "source": {
                        "knowledge_id": knowledge_id,
                        "business_case_id": row.get("business_case_id"),
                        "field": field,
                        "evidence_refs": references,
                        "evidence_scope": "KNOWLEDGE_LEVEL",
                    },
                }
                for field in fields
            ]
            return {
                "status": "COMPLETED", "knowledge_id": knowledge_id,
                "intent": intent, "recommendations": results, "trace": trace,
                "unknowns": list(plan.get("unknowns") or [])[:4],
            }
        except (ValueError, TypeError):
            return {
                "status": "REJECTED",
                "code": "ENGINEERING_EVIDENCE_VALIDATION_FAILED",
                "knowledge_id": knowledge_id, "recommendations": [], "trace": {},
            }
        except Exception as error:
            return {
                "status": "BLOCKED",
                "code": str(getattr(error, "code", None) or type(error).__name__),
                "knowledge_id": knowledge_id, "recommendations": [], "trace": {},
            }
