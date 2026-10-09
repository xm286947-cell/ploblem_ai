"""Read-only, provenance-checked engineering Knowledge consumption.

AI suggestions are never Formal Knowledge. Cite only selected published
projection fields and its Evidence IDs, otherwise fail closed.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

_ALLOWED_FIELDS = {
    "DESIGN_REUSE": {"engineering_rule", "design_constraint", "verification_method", "applicability", "failure_mechanism"},
    "RISK": {"failure_mode", "failure_mechanism", "root_cause", "design_constraint", "diagnostic_clue", "key_parameters"},
    "FIELD_PROBLEM": {"symptom", "occurrence_condition", "root_cause", "actions", "verification_result"},
    "TEST_VALIDATION": {"verification_method", "verification_result", "design_constraint", "engineering_rule", "failure_mechanism"},
}


class HardwareEngineeringAnalysisError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class HardwareEngineeringAnalysisService:
    def __init__(self, consumption_service: Any,
                 agent: Callable[[dict[str, Any]], dict[str, Any]] | None):
        self.consumption = consumption_service
        self.agent = agent

    @staticmethod
    def _field_text(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)

    def analyze(self, knowledge_id: str, scenario: str) -> dict[str, Any]:
        if scenario not in _ALLOWED_FIELDS:
            raise HardwareEngineeringAnalysisError("ANALYSIS_SCENARIO_INVALID")
        projection = self.consumption.get(knowledge_id)
        if not isinstance(projection, Mapping):
            raise HardwareEngineeringAnalysisError("KNOWLEDGE_NOT_FOUND")
        evidence_ids = set(projection.get("evidence_refs") or [])
        if not evidence_ids:
            raise HardwareEngineeringAnalysisError("EVIDENCE_REQUIRED")
        allowed = {field: projection[field] for field in _ALLOWED_FIELDS[scenario]
                   if projection.get(field) not in (None, "", [], {})}
        if not allowed:
            raise HardwareEngineeringAnalysisError("NO_RELEVANT_FORMAL_FIELDS")
        if self.agent is None:
            raise HardwareEngineeringAnalysisError("ENGINEERING_AGENT_NOT_CONFIGURED")
        result = self.agent({
            "knowledge_id": knowledge_id,
            "business_case_id": projection.get("business_case_id"),
            "scenario": scenario,
            "fields": allowed,
            "evidence_refs": sorted(evidence_ids),
        })
        trace = result.get("trace") if isinstance(result, Mapping) else None
        if not isinstance(trace, Mapping) or not trace.get("task_id") or not trace.get("run_id") or int(trace.get("provider_calls") or 0) < 1:
            raise HardwareEngineeringAnalysisError("ANALYSIS_AGENT_TRACE_MISSING")
        data = result.get("data")
        if not isinstance(data, Mapping):
            raise HardwareEngineeringAnalysisError("ANALYSIS_RESULT_INVALID")
        checks = data.get("checks")
        if not isinstance(checks, list) or not checks or len(checks) > 8:
            raise HardwareEngineeringAnalysisError("ANALYSIS_CHECKS_INVALID")
        confirmed = []
        for check in checks:
            if not isinstance(check, Mapping):
                raise HardwareEngineeringAnalysisError("ANALYSIS_CHECK_INVALID")
            field = check.get("source_field")
            evidence = check.get("evidence_id")
            excerpt = check.get("source_excerpt")
            advice = check.get("recommendation")
            if (field not in allowed or evidence not in evidence_ids
                    or not isinstance(excerpt, str) or len(excerpt) < 4
                    or excerpt not in self._field_text(allowed[field])
                    or not isinstance(advice, str) or not advice.strip()):
                raise HardwareEngineeringAnalysisError("ANALYSIS_SOURCE_UNSUPPORTED")
            confirmed.append({
                "recommendation": advice,
                "source_field": field,
                "source_excerpt": excerpt,
                "knowledge_id": knowledge_id,
                "business_case_id": projection.get("business_case_id"),
                "evidence_id": evidence,
                "evidence_scope": "CASE_LEVEL_REFERENCE",
            })
        return {
            "contract_version": "hardware-r2-engineering-analysis/v1",
            "kind": "AI_GENERATED_REFERENCE_NOT_FORMAL",
            "knowledge_id": knowledge_id,
            "business_case_id": projection.get("business_case_id"),
            "scenario": scenario,
            "summary": str(data.get("summary") or ""),
            "checks": confirmed,
            "unknowns": list(data.get("unknowns") or []),
            "trace": dict(trace),
        }
