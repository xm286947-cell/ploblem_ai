"""Storage device-level lifetime assessment composition contract.

This module composes existing Storage domain capabilities.  It deliberately
does not introduce a new lifetime formula or an automatic replacement
decision.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from runtime.contracts import ContractModel
from .lifetime_engine import LifetimeAssessmentResult, LifetimeAssessmentStatus
from .software_impact import SoftwareImpactAnalysisResult, SoftwareImpactAnalysisStatus


class DeviceLifetimeOverallStatus(str, Enum):
    ASSESSED = "ASSESSED"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INVALID = "INVALID"


class DeviceLifetimeAssessment(ContractModel):
    device_id: str
    device_type: str
    overall_status: DeviceLifetimeOverallStatus
    metric_assessments: list[LifetimeAssessmentResult] = Field(default_factory=list)
    endurance_summary: dict[str, Any] = Field(default_factory=dict)
    risk_reasons: list[str] = Field(default_factory=list)
    software_impact: SoftwareImpactAnalysisResult | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    next_required_actions: list[str] = Field(default_factory=list)
    decision_boundary: str = "NO_AUTO_REPLACEMENT_DECISION"
    schema_version: str = "1.0"


class DeviceLifetimeAssessmentComposer:
    """Aggregate existing evidence-backed results into one product response."""

    @staticmethod
    def compose(
        *,
        device_id: str,
        device_type: str,
        requested_metrics: list[str],
        metric_results: list[LifetimeAssessmentResult],
        software_impact: SoftwareImpactAnalysisResult | None = None,
    ) -> DeviceLifetimeAssessment:
        calculated = [item for item in metric_results if item.status is LifetimeAssessmentStatus.CALCULATED]
        invalid = [item for item in metric_results if item.status is LifetimeAssessmentStatus.INVALID_INPUT]
        missing: list[str] = []
        evidence_refs: set[str] = set()
        knowledge_refs: set[str] = set()
        risk_reasons: list[str] = []
        endurance_summary: dict[str, Any] = {}

        for item in metric_results:
            evidence_refs.update(item.evidence_refs)
            knowledge_refs.update(item.knowledge_refs)
            missing.extend(item.missing_inputs)
            missing.extend(item.error_details)

            if item.status is LifetimeAssessmentStatus.CALCULATED:
                if item.formula_id == "SSD_TBW_CONSUMPTION_V1" and isinstance(item.result, dict):
                    endurance_summary["tbw"] = item.result
                    ratio = item.result.get("consumed_ratio")
                    if isinstance(ratio, (int, float)):
                        risk_reasons.append(f"SSD_TBW_CONSUMED_RATIO={ratio}")
                elif item.formula_id == "NVME_PERCENTAGE_USED_INTERPRETATION_V1":
                    endurance_summary["nvme_percentage_used"] = item.result
                    risk_reasons.append(f"NVME_PERCENTAGE_USED={item.result}")
                elif item.formula_id in {
                    "EMMC_DEVICE_LIFE_TIME_A_V1",
                    "EMMC_DEVICE_LIFE_TIME_B_V1",
                    "EMMC_PRE_EOL_V1",
                }:
                    endurance_summary[item.formula_id.lower()] = item.result

        if software_impact is not None:
            evidence_refs.update(software_impact.evidence_refs)
            knowledge_refs.update(software_impact.knowledge_refs)
            missing.extend(software_impact.missing_information)

        if invalid:
            overall = DeviceLifetimeOverallStatus.INVALID
        elif calculated and len(calculated) == len(requested_metrics):
            overall = DeviceLifetimeOverallStatus.ASSESSED
        elif calculated:
            overall = DeviceLifetimeOverallStatus.PARTIAL
        else:
            overall = DeviceLifetimeOverallStatus.INSUFFICIENT_DATA

        assessed_metrics = {item.metric for item in calculated}
        missing_metrics = [
            metric for metric in requested_metrics
            if metric not in assessed_metrics
        ]
        next_actions = [f"PROVIDE_EVIDENCE_FOR:{metric}" for metric in missing_metrics]
        if software_impact is not None and software_impact.status is not SoftwareImpactAnalysisStatus.EVIDENCED:
            next_actions.append("COMPLETE_FORMAL_SOFTWARE_IMPACT_EVIDENCE")

        return DeviceLifetimeAssessment(
            device_id=device_id,
            device_type=device_type,
            overall_status=overall,
            metric_assessments=metric_results,
            endurance_summary=endurance_summary,
            risk_reasons=risk_reasons,
            software_impact=software_impact,
            evidence_refs=sorted(evidence_refs),
            knowledge_refs=sorted(knowledge_refs),
            coverage={
                "requested_metrics": requested_metrics,
                "assessed_metrics": sorted(assessed_metrics),
                "missing_metrics": missing_metrics,
                "assessed_count": len(calculated),
                "requested_count": len(requested_metrics),
            },
            missing_information=sorted(set(missing)),
            next_required_actions=sorted(set(next_actions)),
        )
