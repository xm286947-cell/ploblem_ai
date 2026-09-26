"""Formal Knowledge -> Software Impact projection.

The module consumes the existing repository/service boundary through an
adapter.  It never uses static fallback rules and never makes a replacement
decision.
"""

from __future__ import annotations

from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException
from pydantic import Field

from runtime.contracts import ContractModel


SCHEMA_VERSION = "1.1"


class SoftwareImpactAnalysisStatus(str, Enum):
    EVIDENCED = "EVIDENCED"
    INSUFFICIENT_KNOWLEDGE = "INSUFFICIENT_KNOWLEDGE"
    INSUFFICIENT_FACT = "INSUFFICIENT_FACT"
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


class ValidationItem(ContractModel):
    validation_id: str
    title: str
    description: str
    status: str = "PENDING"
    owner_role: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    source_type: str = "FORMAL_KNOWLEDGE_RELEASE"
    schema_version: str = SCHEMA_VERSION


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
    validation_items: list[ValidationItem] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence_basis: list[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION


class FormalKnowledgeReleaseConsumer(Protocol):
    def released_for(self, usage_context: dict[str, Any], requested_topics: list[str]) -> list[FormalKnowledgeRelease]: ...


class RepositoryFormalKnowledgeReleaseConsumer:
    """Adapter over the existing KnowledgeService repository boundary.

    Release records are read from the existing knowledge repository; this
    module does not create a second knowledge store or call a provider.
    """
    def __init__(self, knowledge_service: Any): self.knowledge_service = knowledge_service

    def released_for(self, usage_context: dict[str, Any], requested_topics: list[str]) -> list[FormalKnowledgeRelease]:
        repository = self.knowledge_service.repository
        candidates = []
        for path in repository.list("knowledge/formal_releases"):
            raw = repository.load(path)
            if not raw or raw.get("release_status") != "RELEASED": continue
            scope = str(raw.get("semantic_scope") or "")
            if requested_topics and not any(topic in scope or topic in raw.get("topics", []) for topic in requested_topics): continue
            candidates.append(FormalKnowledgeRelease(**raw))
        return candidates


class InMemoryFormalKnowledgeReleaseConsumer:
    """Test/integration seam implementing the same formal consumer contract."""
    def __init__(self, releases: list[FormalKnowledgeRelease]): self.releases = releases
    def released_for(self, usage_context, requested_topics):
        return [release for release in self.releases if release.release_status == "RELEASED" and (not requested_topics or any(topic in release.semantic_scope or topic in release.trigger_fact_names for topic in requested_topics))]


class SoftwareImpact(ContractModel):
    impact_id: str
    device_fact_refs: list[str]
    technical_meaning: str
    software_impact: list[str]
    design_concern: list[str]
    monitoring_impact: list[str]
    validation_items: list[ValidationItem]
    knowledge_refs: list[str]
    evidence_refs: list[str]
    confidence_basis: list[str]
    missing_information: list[str] = Field(default_factory=list)
    source_type: str = "FORMAL_KNOWLEDGE_ANALYSIS"
    schema_version: str = SCHEMA_VERSION


class SoftwareImpactAnalysisRequest(ContractModel):
    device_id: str
    usage_context: dict[str, Any] = Field(default_factory=dict)
    requested_topics: list[str] = Field(default_factory=list)
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list)
    formal_knowledge_releases: list[FormalKnowledgeRelease] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION


class SoftwareImpactAnalysisResult(ContractModel):
    analysis_id: str
    device_id: str
    usage_context: dict[str, Any] = Field(default_factory=dict)
    requested_topics: list[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    status: SoftwareImpactAnalysisStatus
    impacts: list[SoftwareImpact] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    confidence_basis: list[str] = Field(default_factory=list)
    replay_trace: dict[str, Any] = Field(default_factory=dict)
    decision_boundary: str = "NO_AUTO_REPLACEMENT_DECISION"


class SoftwareImpactEngine:
    def __init__(self, knowledge_consumer: FormalKnowledgeReleaseConsumer | None = None): self.knowledge_consumer = knowledge_consumer

    @staticmethod
    def _result(request, status, *, impacts=None, knowledge_refs=None, evidence_refs=None, missing=None, confidence=None, trace=None):
        knowledge_refs = sorted(set(knowledge_refs or [])); evidence_refs = sorted(set(evidence_refs or []))
        replay = {"schema_version": request.schema_version, "usage_context": request.usage_context, "requested_topics": request.requested_topics, "knowledge_refs": knowledge_refs, "evidence_refs": evidence_refs, **(trace or {})}
        digest = sha256(json.dumps({"device_id": request.device_id, "replay": replay}, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return SoftwareImpactAnalysisResult(analysis_id=f"IMPACT-{digest}", device_id=request.device_id, usage_context=request.usage_context, requested_topics=request.requested_topics, status=status, impacts=impacts or [], knowledge_refs=knowledge_refs, evidence_refs=evidence_refs, missing_information=missing or [], confidence_basis=confidence or [], replay_trace=replay)

    def _resolve_releases(self, request):
        if request.formal_knowledge_releases: return request.formal_knowledge_releases
        if self.knowledge_consumer is None: return []
        return self.knowledge_consumer.released_for(request.usage_context, request.requested_topics)

    def analyze(self, request: SoftwareImpactAnalysisRequest) -> SoftwareImpactAnalysisResult:
        if any(fact.source_type != "CONFIRMED_DEVICE_FACT" for fact in request.confirmed_facts):
            return self._result(request, SoftwareImpactAnalysisStatus.INVALID_INPUT, missing=["FACT_SOURCE_MUST_BE_CONFIRMED_DEVICE_FACT"])
        releases = self._resolve_releases(request)
        valid_releases = [release for release in releases if release.release_status == "RELEASED" and release.evidence_refs]
        if not valid_releases:
            return self._result(request, SoftwareImpactAnalysisStatus.INSUFFICIENT_KNOWLEDGE, missing=["FORMAL_KNOWLEDGE_RELEASE_WITH_EVIDENCE_REQUIRED"])
        facts_by_name = {fact.metric_name: fact for fact in request.confirmed_facts}
        missing_fact_evidence = [fact.metric_name for fact in request.confirmed_facts if not fact.evidence_refs]
        if missing_fact_evidence:
            return self._result(request, SoftwareImpactAnalysisStatus.INSUFFICIENT_FACT, knowledge_refs=[r.release_id for r in valid_releases], missing=[f"FACT_EVIDENCE_REQUIRED:{name}" for name in missing_fact_evidence])
        impacts = []; gaps = []
        for release in valid_releases:
            missing_triggers = [name for name in release.trigger_fact_names if name not in facts_by_name]
            if missing_triggers:
                gaps.extend(f"{release.release_id}:TRIGGER_FACT_REQUIRED:{name}" for name in missing_triggers); continue
            if not release.technical_meaning or not release.software_impact:
                gaps.append(f"{release.release_id}:FORMAL_IMPACT_CONTENT_REQUIRED"); continue
            fact_refs = [facts_by_name[name].fact_id for name in release.trigger_fact_names]
            fact_evidence = [ref for name in release.trigger_fact_names for ref in facts_by_name[name].evidence_refs]
            validation = [item.model_copy(deep=True) for item in release.validation_items]
            if not validation: gaps.append(f"{release.release_id}:VALIDATION_ITEMS_REQUIRED"); continue
            impacts.append(SoftwareImpact(impact_id=f"{request.device_id}:{release.release_id}", device_fact_refs=fact_refs, technical_meaning=release.technical_meaning, software_impact=list(release.software_impact), design_concern=list(release.design_concern), monitoring_impact=list(release.monitoring_impact), validation_items=validation, knowledge_refs=[release.release_id], evidence_refs=sorted(set(fact_evidence + release.evidence_refs + [ref for item in validation for ref in item.evidence_refs])), confidence_basis=sorted(set(["CONFIRMED_DEVICE_FACT", "FORMAL_KNOWLEDGE_RELEASE", "EVIDENCE_TRACEABLE", *release.confidence_basis]))) )
        if not impacts:
            return self._result(request, SoftwareImpactAnalysisStatus.INSUFFICIENT_FACT, knowledge_refs=[r.release_id for r in valid_releases], evidence_refs=[ref for r in valid_releases for ref in r.evidence_refs], missing=sorted(set(gaps or ["NO_TRIGGERED_FORMAL_IMPACT"])))
        return self._result(request, SoftwareImpactAnalysisStatus.EVIDENCED, impacts=impacts, knowledge_refs=[ref for item in impacts for ref in item.knowledge_refs], evidence_refs=[ref for item in impacts for ref in item.evidence_refs], missing=sorted(set(gaps)), confidence=["FACT_ANALYSIS_SEPARATION", "FORMAL_KNOWLEDGE_ONLY", "EVIDENCE_TRACEABLE"], trace={"impact_ids": [item.impact_id for item in impacts]})


class SoftwareImpactAnalysisRepository:
    def __init__(self): self._items = {}
    def save(self, result): self._items[result.analysis_id] = result; return result
    def get(self, analysis_id): return self._items.get(analysis_id)


def create_software_impact_router(engine=None, repository=None):
    router = APIRouter(); engine = engine or SoftwareImpactEngine(); repository = repository or SoftwareImpactAnalysisRepository()
    @router.post("/storage/software-impact/analyze", status_code=201)
    def analyze(payload: SoftwareImpactAnalysisRequest): return repository.save(engine.analyze(payload)).model_dump(mode="json")
    @router.get("/storage/software-impact/{analysis_id}")
    def get_analysis(analysis_id: str):
        item = repository.get(analysis_id)
        if item is None: raise HTTPException(404, "SOFTWARE_IMPACT_ANALYSIS_NOT_FOUND")
        return item.model_dump(mode="json")
    return router
