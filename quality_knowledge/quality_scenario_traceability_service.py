"""QS-MVP-06 Quality Scenario V1 Evidence traceability.

This service validates the already-frozen Source/Evidence references. It never
retrieves model logs, never invents source text, and does not create a second
Evidence store.
"""
from __future__ import annotations

from typing import Any

from quality_knowledge.quality_scenario_v1 import (
    QualityScenarioFieldsV1,
    QualityScenarioV1,
)
from quality_knowledge.quality_scenario_v1_store import QualityScenarioV1Repository


VALID_SUPPORT_FIELDS = frozenset(QualityScenarioFieldsV1.model_fields)


class QualityScenarioTraceabilityService:
    def __init__(self, repository: QualityScenarioV1Repository):
        self.repository = repository

    @staticmethod
    def _content_status(source_text: str, content_ref: str) -> str:
        if str(source_text or "").strip():
            return "INLINE_SOURCE_TEXT"
        if str(content_ref or "").strip():
            return "CONTROLLED_CONTENT_REF"
        return "MISSING_CONTENT_POINTER"

    def trace(self, scenario_id: str) -> dict[str, Any]:
        scenario = self.repository.get(scenario_id)
        if scenario is None:
            raise ValueError("QUALITY_SCENARIO_V1_NOT_FOUND")
        return self._trace_scenario(scenario)

    def _trace_scenario(self, scenario: QualityScenarioV1) -> dict[str, Any]:
        source_map = {item.source_ref: item for item in scenario.source_problem_refs}
        evidence_by_source: dict[str, list[str]] = {
            source_ref: [] for source_ref in source_map
        }
        issues: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        evidence_payload: list[dict[str, Any]] = []

        for evidence in scenario.evidence_refs:
            linked = evidence.source_ref in source_map
            if linked:
                evidence_by_source[evidence.source_ref].append(evidence.evidence_id)
            else:
                issues.append(
                    {
                        "code": "EVIDENCE_SOURCE_REF_NOT_FOUND",
                        "evidence_id": evidence.evidence_id,
                        "detail": evidence.source_ref,
                    }
                )

            unknown_supports = sorted(
                support
                for support in evidence.supports
                if support not in VALID_SUPPORT_FIELDS
            )
            if unknown_supports:
                for support in unknown_supports:
                    issues.append(
                        {
                            "code": "UNKNOWN_SUPPORT_FIELD",
                            "evidence_id": evidence.evidence_id,
                            "detail": support,
                        }
                    )

            content_status = self._content_status(
                evidence.source_text,
                evidence.content_ref,
            )
            if content_status == "MISSING_CONTENT_POINTER":
                issues.append(
                    {
                        "code": "EVIDENCE_CONTENT_POINTER_MISSING",
                        "evidence_id": evidence.evidence_id,
                        "detail": "",
                    }
                )

            evidence_payload.append(
                {
                    **evidence.model_dump(mode="json"),
                    "source_resolved": linked,
                    "supports_valid": not unknown_supports,
                    "unknown_supports": unknown_supports,
                    "content_status": content_status,
                }
            )

        source_payload = []
        for source in scenario.source_problem_refs:
            linked_evidence = evidence_by_source.get(source.source_ref, [])
            if not linked_evidence:
                warnings.append(
                    {
                        "code": "SOURCE_WITHOUT_DIRECT_EVIDENCE",
                        "source_ref": source.source_ref,
                        "detail": "",
                    }
                )
            source_payload.append(
                {
                    **source.model_dump(mode="json"),
                    "evidence_ids": linked_evidence,
                }
            )

        if not scenario.source_problem_refs:
            issues.append(
                {
                    "code": "SCENARIO_SOURCE_PROBLEM_REQUIRED",
                    "evidence_id": "",
                    "detail": "",
                }
            )
        if not scenario.evidence_refs:
            issues.append(
                {
                    "code": "SCENARIO_EVIDENCE_REQUIRED",
                    "evidence_id": "",
                    "detail": "",
                }
            )

        return {
            "scenario_id": scenario.scenario_id,
            "scenario_version": scenario.scenario_version,
            "status": scenario.status.value,
            "integrity": {
                "status": "PASS" if not issues else "BLOCKED",
                "issues": issues,
                "warnings": warnings,
                "source_count": len(source_payload),
                "evidence_count": len(evidence_payload),
            },
            "sources": source_payload,
            "evidence": evidence_payload,
            "valid_support_fields": sorted(VALID_SUPPORT_FIELDS),
        }

    def scenarios_for_source(self, source_ref: str) -> dict[str, Any]:
        normalized = str(source_ref or "").strip()
        if not normalized:
            raise ValueError("QUALITY_SCENARIO_SOURCE_REF_REQUIRED")
        finder = getattr(self.repository, "list_by_source", None)
        if finder is None:
            raise ValueError("QUALITY_SCENARIO_SOURCE_REVERSE_LOOKUP_UNAVAILABLE")
        items = finder(normalized)
        return {
            "source_ref": normalized,
            "items": [item.model_dump(mode="json") for item in items],
            "total": len(items),
        }


__all__ = [
    "VALID_SUPPORT_FIELDS",
    "QualityScenarioTraceabilityService",
]
