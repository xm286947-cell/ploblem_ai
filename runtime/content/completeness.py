from __future__ import annotations

from runtime.contracts import (
    CompletenessGateResult,
    Coverage,
    MergeResult,
)


class CompletenessGateEvaluator:
    def evaluate(
        self,
        *,
        coverage: Coverage | list[Coverage] | None = None,
        schema_valid: bool = True,
        merge_result: MergeResult | None = None,
        merge_complete: bool | None = None,
        evidence_integrity: bool = True,
        business_gate_passed: bool | None = None,
        gate_ref: str | None = None,
        gate_version: str | None = None,
    ) -> CompletenessGateResult:
        if coverage is None:
            coverage_complete = None
        elif isinstance(coverage, list):
            coverage_complete = all(item.complete for item in coverage)
        else:
            coverage_complete = coverage.complete

        if merge_result is not None:
            resolved_merge_complete = merge_result.complete
        elif merge_complete is None:
            resolved_merge_complete = True
        else:
            resolved_merge_complete = merge_complete

        reasons: list[str] = []
        if coverage_complete is False:
            reasons.append("SOURCE_COVERAGE_INCOMPLETE")
        if not schema_valid:
            reasons.append("SCHEMA_INVALID")
        if not resolved_merge_complete:
            reasons.append("MERGE_INCOMPLETE")
        if not evidence_integrity:
            reasons.append("EVIDENCE_INTEGRITY_FAILED")
        if business_gate_passed is False:
            reasons.append("BUSINESS_GATE_FAILED")

        return CompletenessGateResult(
            source_coverage_complete=coverage_complete,
            schema_valid=schema_valid,
            merge_complete=resolved_merge_complete,
            evidence_integrity=evidence_integrity,
            business_gate_passed=business_gate_passed,
            passed=not reasons,
            reasons=reasons,
            gate_ref=gate_ref,
            gate_version=gate_version,
        )
