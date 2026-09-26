"""Storage Product composition for the frozen T1/T2/T3 domain contracts.

This is the single product-side seam. It uses the existing Storage SQLite
boundary and the existing product fact and KnowledgeRelease surfaces.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from runtime.observation import RuntimeObservationError, RuntimeObservationService
from runtime.store.observation import RuntimeObservationRepository
from runtime.contracts import RuntimeObservation

from . import core, product_api
from .knowledge_release import KnowledgeReleaseConsumer, KnowledgeReleaseError
from .lifetime_engine import (
    ConfirmedFact as LifetimeFact,
    FormalKnowledgeReference,
    FormulaRegistry,
    LifetimeAssessmentRequest,
    LifetimeEngine,
    LifetimeAssessmentStatus,
)
from .software_impact import (
    ConfirmedFact as ImpactFact,
    FormalKnowledgeRelease,
    SoftwareImpactAnalysisRequest,
    SoftwareImpactEngine,
    ValidationItem,
    ValidationItemStatus,
    ValidationItemType,
)


def _ensure_schema() -> None:
    with core.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS lifetime_assessment(
                assessment_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                record_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS software_impact_analysis(
                analysis_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, device_id TEXT NOT NULL,
                record_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_lifetime_assessment_device ON lifetime_assessment(device_id);
            CREATE INDEX IF NOT EXISTS idx_software_impact_device ON software_impact_analysis(device_id);
            """
        )


def _evidence_refs(items: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for key in ("evidence_id", "source_ref", "source_id"):
            value = str(item.get(key) or "").strip()
            if value and value not in refs:
                refs.append(value)
    return refs


def _fact_metric(raw: dict[str, Any]) -> str:
    value = str(raw.get("canonical_name") or raw.get("parameter_name") or "").strip()
    aliases = {
        "tbw": "rated_tbw_bytes", "host_written": "host_written_bytes",
        "capacity": "capacity_bytes", "pe_cycles": "rated_pe_cycles",
        "erase_count": "erase_count", "percentage_used": "percentage_used",
        "pre_eol": "pre_eol_info", "life_time_a": "device_life_time_a",
        "life_time_b": "device_life_time_b",
    }
    return aliases.get(value.lower(), value)


class StorageEngineeringInsightService:
    def __init__(self) -> None:
        _ensure_schema()
        self.runtime = RuntimeObservationService(RuntimeObservationRepository(core.DB))

    def _confirmed_facts(self, device_id: str, supplied: list[dict[str, Any]] | None, *, impact: bool = False):
        if supplied is not None:
            model = ImpactFact if impact else LifetimeFact
            return [model(**item) for item in supplied]
        payload = product_api.confirmed_device_facts(device_id)
        facts = []
        for raw in payload.get("facts") or []:
            refs = _evidence_refs(raw.get("evidence") or [])
            facts.append((ImpactFact if impact else LifetimeFact)(
                fact_id=f"{device_id}:{_fact_metric(raw)}",
                metric_name=_fact_metric(raw), value=raw.get("value"), unit=raw.get("unit") or None,
                condition=raw.get("condition") or None, scope=raw.get("scope") or None,
                evidence_refs=refs, source_type="CONFIRMED_DEVICE_FACT",
            ))
        return facts

    @staticmethod
    def _knowledge_objects(query: str, device_type: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
        consumer = KnowledgeReleaseConsumer.current()
        status = consumer.status()
        if not status.get("available"):
            return status, []
        try:
            return status, consumer.query(query, device_type=device_type).get("results") or []
        except KnowledgeReleaseError:
            return status, []

    def _lifetime_knowledge(self, metric: str, device_type: str = "") -> list[FormalKnowledgeReference]:
        status, objects = self._knowledge_objects(metric, device_type)
        release_version = str(status.get("knowledge_release_version") or "")
        mapped = []
        for obj in objects:
            parameters = obj.get("parameters") or obj.get("structured_parameters") or {}
            if not isinstance(parameters, dict):
                parameters = {}
            mapped.append(FormalKnowledgeReference(
                knowledge_id=str(obj.get("object_id") or ""), release_version=str(obj.get("knowledge_release_version") or release_version),
                release_status="RELEASED" if str(obj.get("status") or "") == "ACTIVE" else str(obj.get("status") or ""),
                semantic_scope=str(obj.get("title") or obj.get("summary") or metric),
                evidence_refs=[str(x) for x in obj.get("evidence_refs") or []], parameters=parameters,
            ))
        return mapped

    def _impact_knowledge(self, topics: list[str], device_type: str = "") -> list[FormalKnowledgeRelease]:
        status, objects = self._knowledge_objects(" ".join(topics), device_type)
        release_version = str(status.get("knowledge_release_version") or "")
        mapped: list[FormalKnowledgeRelease] = []
        for obj in objects:
            required = ("trigger_fact_names", "technical_meaning", "software_impact", "validation_items")
            if not all(obj.get(key) for key in required):
                continue
            validation: list[ValidationItem] = []
            try:
                for item in obj.get("validation_items") or []:
                    validation.append(ValidationItem(
                        validation_id=str(item["validation_id"]), trigger_ref=str(item["trigger_ref"]),
                        type=ValidationItemType(str(item["type"])), description=str(item["description"]),
                        status=ValidationItemStatus(str(item.get("status") or "PENDING")),
                        evidence_refs=[str(x) for x in item.get("evidence_refs") or []], title=item.get("title"),
                    ))
            except (KeyError, TypeError, ValueError):
                continue
            evidence_refs = [str(x) for x in obj.get("evidence_refs") or []]
            if not evidence_refs:
                continue
            mapped.append(FormalKnowledgeRelease(
                release_id=str(obj.get("object_id") or ""), release_version=str(obj.get("knowledge_release_version") or release_version),
                release_status="RELEASED" if str(obj.get("status") or "") == "ACTIVE" else str(obj.get("status") or ""),
                semantic_scope=str(obj.get("title") or obj.get("summary") or ""),
                trigger_fact_names=[str(x) for x in obj.get("trigger_fact_names") or []],
                technical_meaning=str(obj["technical_meaning"]), software_impact=[str(x) for x in obj["software_impact"]],
                design_concern=[str(x) for x in obj.get("design_concern") or []], monitoring_impact=[str(x) for x in obj.get("monitoring_impact") or []],
                validation_items=validation, evidence_refs=evidence_refs,
            ))
        return mapped

    def create_observation(self, observation: RuntimeObservation) -> RuntimeObservation:
        return self.runtime.create(observation)

    def assess_lifetime(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload); metric = str(body.pop("metric") or "")
        device_type = str(body.pop("device_type", "") or "")
        device_id = str(body["device_id"])
        facts = self._confirmed_facts(device_id, body.pop("confirmed_facts", None))
        knowledge = self._lifetime_knowledge(metric, device_type)
        # Runtime is read only from the canonical T1 repository.  Callers
        # cannot inject an unpersisted value into a formal T2 assessment.
        runtime_observations = self.runtime.list(device_id=device_id)
        request = LifetimeAssessmentRequest(
            **body,
            confirmed_facts=facts,
            runtime_observations=runtime_observations,
            formal_knowledge=knowledge,
        )
        result = LifetimeEngine().assess(request, metric)
        record = result.model_dump(mode="json")
        with core.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO lifetime_assessment(assessment_id,device_id,record_json) VALUES (?,?,?)", (result.assessment_id, result.device_id, json.dumps(record, ensure_ascii=False)))
        return record

    def get_lifetime(self, assessment_id: str) -> dict[str, Any] | None:
        with core.connect() as connection:
            row = connection.execute("SELECT record_json FROM lifetime_assessment WHERE assessment_id=?", (assessment_id,)).fetchone()
        return json.loads(row["record_json"]) if row else None

    def analyze_impact(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload); device_id = str(body["device_id"]); topics = [str(x) for x in body.get("requested_topics") or []]
        device_type = str(body.pop("device_type", "") or "")
        facts = self._confirmed_facts(device_id, body.pop("confirmed_facts", None), impact=True)
        releases = self._impact_knowledge(topics, device_type)
        request = SoftwareImpactAnalysisRequest(**body, confirmed_facts=facts, formal_knowledge_releases=releases)
        result = SoftwareImpactEngine().analyze(request)
        record = result.model_dump(mode="json")
        with core.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO software_impact_analysis(analysis_id,request_id,device_id,record_json) VALUES (?,?,?,?)", (result.analysis_id, result.request_id, result.device_id, json.dumps(record, ensure_ascii=False)))
        return record

    def get_impact(self, analysis_id: str) -> dict[str, Any] | None:
        with core.connect() as connection:
            row = connection.execute("SELECT record_json FROM software_impact_analysis WHERE analysis_id=?", (analysis_id,)).fetchone()
        return json.loads(row["record_json"]) if row else None


def create_engineering_insight_router(service: StorageEngineeringInsightService | None = None) -> APIRouter:
    service = service or StorageEngineeringInsightService()
    router = APIRouter()

    @router.post("/storage/runtime-observations", status_code=201)
    def create_observation(payload: RuntimeObservation):
        try:
            item = service.create_observation(payload)
            return {**item.model_dump(mode="json"), "formal_consumption": RuntimeObservationService.formal_consumption(item)}
        except RuntimeObservationError as error:
            raise HTTPException(409 if "ALREADY_EXISTS" in error.code else 400, {"code": error.code, "details": error.details}) from error

    @router.get("/storage/devices/{device_id}/runtime-observations")
    def list_observations(device_id: str, metric_name: str | None = None, limit: int = 100):
        items = service.runtime.list(device_id=device_id, metric_name=metric_name, limit=limit)
        return {"items": [item.model_dump(mode="json") for item in items], "total": len(items)}

    @router.get("/storage/runtime-observations/{observation_id}")
    def get_observation(observation_id: str):
        item = service.runtime.get(observation_id)
        if item is None: raise HTTPException(404, "RUNTIME_OBSERVATION_NOT_FOUND")
        return item.model_dump(mode="json")

    @router.post("/storage/lifetime/assess", status_code=201)
    def assess_lifetime(payload: dict[str, Any]):
        try: return service.assess_lifetime(payload)
        except (KeyError, ValueError) as error: raise HTTPException(422, str(error)) from error

    @router.get("/storage/lifetime/assessments/{assessment_id}")
    def get_lifetime(assessment_id: str):
        record = service.get_lifetime(assessment_id)
        if record is None: raise HTTPException(404, "LIFETIME_ASSESSMENT_NOT_FOUND")
        return record

    @router.get("/storage/lifetime/formulas")
    def list_formulas(): return {"items": [spec.model_dump(mode="json") for spec in FormulaRegistry.SPECS.values()]}

    @router.get("/storage/lifetime/formulas/{formula_id:path}")
    def get_formula(formula_id: str):
        try: return FormulaRegistry.describe(formula_id).model_dump(mode="json")
        except ValueError as error: raise HTTPException(404, str(error)) from error

    @router.post("/storage/software-impact/analyze", status_code=201)
    def analyze_impact(payload: dict[str, Any]):
        return service.analyze_impact(payload)

    @router.get("/storage/software-impact/{analysis_id}")
    def get_impact(analysis_id: str):
        record = service.get_impact(analysis_id)
        if record is None: raise HTTPException(404, "SOFTWARE_IMPACT_ANALYSIS_NOT_FOUND")
        return record

    return router
