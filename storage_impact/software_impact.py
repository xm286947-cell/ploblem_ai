"""Formal-Knowledge-only software impact analysis.

The engine intentionally has no default impact conclusions.  A release must
carry the impact content and its own Evidence; otherwise the result is
fail-closed with missing information.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from runtime.contracts import ContractModel


class SoftwareImpactAnalysisStatus(str, Enum):
    CALCULATED = "CALCULATED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INVALID_INPUT = "INVALID_INPUT"


class ConfirmedFact(ContractModel):
    fact_id: str
    metric_name: str
    value: Any
    unit: str | None = None
    scope: str | None = None
    condition: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    source_type: str = "CONFIRMED_DEVICE_FACT"


class FormalKnowledgeRelease(ContractModel):
    release_id: str
    release_version: str
    release_status: str = "RELEASED"
    semantic_scope: str
    trigger_fact_names: list[str] = Field(default_factory=list)
    technical_meaning: str | None = None
    software_impact: list[str] = Field(default_factory=list)
    design_concern: list[str] = Field(default_factory=list)
    monitoring_impact: list[str] = Field(default_factory=list)
    validation_items: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence_basis: list[str] = Field(default_factory=list)


class SoftwareImpact(ContractModel):
    impact_id: str
    device_fact_refs: list[str]
    technical_meaning: str
    software_impact: list[str]
    design_concern: list[str]
    monitoring_impact: list[str]
    validation_items: list[str]
    knowledge_refs: list[str]
    evidence_refs: list[str]
    confidence_basis: list[str]
    missing_information: list[str] = Field(default_factory=list)
    source_type: str = "FORMAL_KNOWLEDGE_ANALYSIS"


class SoftwareImpactAnalysisRequest(ContractModel):
    device_id: str
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list)
    formal_knowledge_releases: list[FormalKnowledgeRelease] = Field(default_factory=list)


class SoftwareImpactAnalysisResult(ContractModel):
    analysis_id: str
    device_id: str
    status: SoftwareImpactAnalysisStatus
    impacts: list[SoftwareImpact] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    confidence_basis: list[str] = Field(default_factory=list)
    decision_boundary: str = "NO_AUTO_REPLACEMENT_DECISION"


class SoftwareImpactEngine:
    """Deterministic projection of released knowledge into impact objects."""

    @staticmethod
    def _result(request, status, *, impacts=None, knowledge_refs=None, evidence_refs=None, missing=None, confidence=None):
        return SoftwareImpactAnalysisResult(
            analysis_id=f"IMPACT-{request.device_id}",
            device_id=request.device_id,
            status=status,
            impacts=impacts or [],
            knowledge_refs=sorted(set(knowledge_refs or [])),
            evidence_refs=sorted(set(evidence_refs or [])),
            missing_information=missing or [],
            confidence_basis=confidence or [],
        )

    def analyze(self, request: SoftwareImpactAnalysisRequest) -> SoftwareImpactAnalysisResult:
        if any(fact.source_type != "CONFIRMED_DEVICE_FACT" for fact in request.confirmed_facts):
            return self._result(
                request,
                SoftwareImpactAnalysisStatus.INVALID_INPUT,
                missing=["FACT_SOURCE_MUST_BE_CONFIRMED_DEVICE_FACT"],
            )

        facts_by_name = {fact.metric_name: fact for fact in request.confirmed_facts}
        missing_fact_evidence = [
            fact.metric_name for fact in request.confirmed_facts if not fact.evidence_refs
        ]
        releases = [
            release
            for release in request.formal_knowledge_releases
            if release.release_status == "RELEASED" and release.evidence_refs
        ]
        if not releases:
            return self._result(
                request,
                SoftwareImpactAnalysisStatus.INSUFFICIENT_DATA,
                missing=["FORMAL_KNOWLEDGE_RELEASE_WITH_EVIDENCE_REQUIRED"],
            )
        if missing_fact_evidence:
            return self._result(
                request,
                SoftwareImpactAnalysisStatus.INSUFFICIENT_DATA,
                knowledge_refs=[release.release_id for release in releases],
                missing=[f"FACT_EVIDENCE_REQUIRED:{name}" for name in missing_fact_evidence],
            )

        impacts: list[SoftwareImpact] = []
        missing_information: list[str] = []
        for release in releases:
            missing_triggers = [
                name for name in release.trigger_fact_names if name not in facts_by_name
            ]
            if missing_triggers:
                missing_information.extend(
                    f"{release.release_id}:TRIGGER_FACT_REQUIRED:{name}"
                    for name in missing_triggers
                )
                continue
            if not release.technical_meaning or not release.software_impact:
                missing_information.append(
                    f"{release.release_id}:FORMAL_IMPACT_CONTENT_REQUIRED"
                )
                continue
            fact_refs = [facts_by_name[name].fact_id for name in release.trigger_fact_names]
            fact_evidence = [
                ref for name in release.trigger_fact_names for ref in facts_by_name[name].evidence_refs
            ]
            impacts.append(
                SoftwareImpact(
                    impact_id=f"{request.device_id}:{release.release_id}",
                    device_fact_refs=fact_refs,
                    technical_meaning=release.technical_meaning,
                    software_impact=list(release.software_impact),
                    design_concern=list(release.design_concern),
                    monitoring_impact=list(release.monitoring_impact),
                    validation_items=list(release.validation_items),
                    knowledge_refs=[release.release_id],
                    evidence_refs=sorted(set(fact_evidence + release.evidence_refs)),
                    confidence_basis=sorted(set([
                        "CONFIRMED_DEVICE_FACT",
                        "FORMAL_KNOWLEDGE_RELEASE",
                        "EVIDENCE_TRACEABLE",
                        *release.confidence_basis,
                    ])),
                )
            )

        if not impacts:
            return self._result(
                request,
                SoftwareImpactAnalysisStatus.INSUFFICIENT_DATA,
                knowledge_refs=[release.release_id for release in releases],
                evidence_refs=[ref for release in releases for ref in release.evidence_refs],
                missing=sorted(set(missing_information or ["NO_TRIGGERED_FORMAL_IMPACT"])),
            )
        return self._result(
            request,
            SoftwareImpactAnalysisStatus.CALCULATED,
            impacts=impacts,
            knowledge_refs=[impact.knowledge_refs[0] for impact in impacts],
            evidence_refs=[ref for impact in impacts for ref in impact.evidence_refs],
            missing=sorted(set(missing_information)),
            confidence=["FACT_ANALYSIS_SEPARATION", "FORMAL_KNOWLEDGE_ONLY"],
        )
