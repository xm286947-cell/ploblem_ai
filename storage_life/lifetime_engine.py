"""Deterministic, evidence-traceable Storage lifetime assessment."""

from __future__ import annotations

from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from statistics import fmean
from typing import Any, Iterable

from fastapi import APIRouter, HTTPException
from pydantic import Field

from runtime.contracts import ContractModel, RuntimeObservation


class LifetimeAssessmentStatus(str, Enum):
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


class FormalKnowledgeReference(ContractModel):
    knowledge_id: str
    release_version: str
    release_status: str = "RELEASED"
    semantic_scope: str
    evidence_refs: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class LifetimeAssumption(ContractModel):
    name: str
    value: Any
    unit: str | None = None
    rationale: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class LifetimeAssessmentRequest(ContractModel):
    device_id: str
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list)
    runtime_observations: list[RuntimeObservation] = Field(default_factory=list)
    formal_knowledge: list[FormalKnowledgeReference] = Field(default_factory=list)
    assumptions: list[LifetimeAssumption] = Field(default_factory=list)
    schema_version: str = "1.1"


class FormulaSpec(ContractModel):
    formula_id: str
    formula_version: str
    description: str
    output_unit: str
    required_inputs: list[str] = Field(default_factory=list)
    required_knowledge_parameters: list[str] = Field(default_factory=list)
    protocol_semantics: bool = False


class LifetimeAssessmentResult(ContractModel):
    assessment_id: str
    device_id: str
    metric: str
    status: LifetimeAssessmentStatus
    formula_id: str
    formula_version: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[LifetimeAssumption] = Field(default_factory=list)
    result: Any = None
    unit: str | None = None
    scope: str | None = None
    condition: str | None = None
    confidence_basis: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    replay_trace: dict[str, Any] = Field(default_factory=dict)
    missing_inputs: list[str] = Field(default_factory=list)
    error_details: list[str] = Field(default_factory=list)


class FormulaRegistry:
    """Frozen, inspectable formula registry; no hidden constants."""
    SPECS = {
        "ssd.tbw": FormulaSpec(formula_id="ssd.tbw", formula_version="1.1", description="host bytes / rated TBW and remaining bytes", output_unit="bytes_and_ratio", required_inputs=["host_written_bytes", "rated_tbw_bytes"]),
        "ssd.dwpd": FormulaSpec(formula_id="ssd.dwpd", formula_version="1.1", description="host bytes / capacity bytes / elapsed days", output_unit="drive_writes_per_day", required_inputs=["host_written_bytes", "capacity_bytes", "time_window_days"]),
        "nvme.data_units_written": FormulaSpec(formula_id="nvme.data_units_written", formula_version="1.1", description="NVMe data units multiplied by released bytes_per_data_unit", output_unit="bytes", required_inputs=["data_units_written"], required_knowledge_parameters=["bytes_per_data_unit"], protocol_semantics=True),
        "nvme.percentage_used": FormulaSpec(formula_id="nvme.percentage_used", formula_version="1.1", description="NVMe Percentage Used as reported percent", output_unit="%", required_inputs=["percentage_used"], protocol_semantics=True),
        "emmc.device_life_time_a": FormulaSpec(formula_id="emmc.device_life_time_a", formula_version="1.1", description="eMMC DEVICE_LIFE_TIME_EST_TYP_A interpretation", output_unit="protocol_tier", required_inputs=["device_life_time_a"], required_knowledge_parameters=["life_time_a_map"], protocol_semantics=True),
        "emmc.device_life_time_b": FormulaSpec(formula_id="emmc.device_life_time_b", formula_version="1.1", description="eMMC DEVICE_LIFE_TIME_EST_TYP_B interpretation", output_unit="protocol_tier", required_inputs=["device_life_time_b"], required_knowledge_parameters=["life_time_b_map"], protocol_semantics=True),
        "emmc.pre_eol_info": FormulaSpec(formula_id="emmc.pre_eol_info", formula_version="1.1", description="eMMC PRE_EOL_INFO lifecycle warning interpretation", output_unit="protocol_tier", required_inputs=["pre_eol_info"], required_knowledge_parameters=["pre_eol_map"], protocol_semantics=True),
        "emmc.life_time": FormulaSpec(formula_id="emmc.life_time", formula_version="1.1", description="backward-compatible aggregate of three independent eMMC fields", output_unit="protocol_tier", required_inputs=["device_life_time_a", "device_life_time_b", "pre_eol_info"], required_knowledge_parameters=["life_time_a_map", "life_time_b_map", "pre_eol_map"], protocol_semantics=True),
        "nand.pe_margin": FormulaSpec(formula_id="nand.pe_margin", formula_version="1.1", description="rated P/E cycles minus observed P/E cycles; negative is preserved", output_unit="cycles", required_inputs=["rated_pe_cycles", "pe_cycle_or_erase_count"]),
        "nand.wear_distribution": FormulaSpec(formula_id="nand.wear_distribution", formula_version="1.1", description="deterministic min/max/mean/spread", output_unit="cycles", required_inputs=["wear_distribution"]),
        "generic.waf": FormulaSpec(formula_id="generic.waf", formula_version="1.1", description="media written bytes / host written bytes", output_unit="ratio", required_inputs=["media_written_bytes", "host_written_bytes"]),
    }
    FORMULAS = {key: (spec.formula_id, spec.formula_version) for key, spec in SPECS.items()}

    @classmethod
    def resolve(cls, metric: str) -> tuple[str, str]:
        try: return cls.FORMULAS[metric]
        except KeyError as error: raise ValueError(f"FORMULA_NOT_REGISTERED:{metric}") from error

    @classmethod
    def describe(cls, metric: str) -> FormulaSpec:
        try: return cls.SPECS[metric]
        except KeyError as error: raise ValueError(f"FORMULA_NOT_REGISTERED:{metric}") from error

    @classmethod
    def list(cls) -> dict[str, tuple[str, str]]: return dict(cls.FORMULAS)


class _InsufficientData(Exception):
    def __init__(self, missing: list[str]): self.missing = missing


class _InvalidInput(Exception):
    def __init__(self, details: list[str]): self.details = details


class LifetimeEngine:
    """Pure deterministic engine with explicit source and replay trace."""
    def __init__(self, registry: type[FormulaRegistry] = FormulaRegistry): self.registry = registry

    @staticmethod
    def _fact(request, *names):
        for fact in request.confirmed_facts:
            if fact.metric_name in names: return fact.value, fact
        return None, None

    @staticmethod
    def _observation(request, *names):
        for observation in request.runtime_observations:
            if observation.metric_name in names:
                return (observation.normalized_value, observation) if observation.is_formally_consumable else (None, None)
        return None, None

    @staticmethod
    def _assumption(request, *names):
        for assumption in request.assumptions:
            if assumption.name in names: return assumption.value, assumption
        return None, None

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, bool): raise _InvalidInput([f"{name}:BOOLEAN_NOT_NUMERIC"])
        try: number = float(value)
        except (TypeError, ValueError) as error: raise _InvalidInput([f"{name}:NOT_NUMERIC"]) from error
        if not isfinite(number): raise _InvalidInput([f"{name}:NOT_FINITE"])
        return number

    @staticmethod
    def _unit(value: str | None, expected: set[str], name: str) -> None:
        if not value: raise _InvalidInput([f"{name}:UNIT_REQUIRED"])
        if value not in expected: raise _InvalidInput([f"{name}:UNIT_INVALID:{value}"])

    @staticmethod
    def _knowledge(request): return [item for item in request.formal_knowledge if item.release_status == "RELEASED" and item.evidence_refs]

    @classmethod
    def _require_knowledge(cls, request, metric):
        knowledge = cls._knowledge(request)
        if not knowledge: raise _InsufficientData([f"{metric}:FORMAL_KNOWLEDGE_RELEASE_REQUIRED"])
        return knowledge

    @staticmethod
    def _fact_refs(fact): return list(fact.evidence_refs) if fact else []

    @staticmethod
    def _obs_refs(observation):
        return [observation.evidence_ref] if observation and observation.evidence_ref else []

    @staticmethod
    def _trace(name, value, fact=None, observation=None, assumption=None):
        if fact: return {"name": name, "source_type": fact.source_type, "source_id": fact.fact_id, "value": value, "unit": fact.unit, "evidence_refs": list(fact.evidence_refs)}
        if observation: return {"name": name, "source_type": "RUNTIME_OBSERVATION", "source_id": observation.observation_id, "value": value, "unit": observation.unit, "evidence_refs": [observation.evidence_ref] if observation.evidence_ref else [], "raw_output_ref": observation.raw_output_ref, "capture_time": observation.capture_time.isoformat() if observation.capture_time else None}
        return {"name": name, "source_type": "ASSUMPTION", "source_id": assumption.name if assumption else None, "value": value, "unit": assumption.unit if assumption else None, "evidence_refs": list(assumption.evidence_refs) if assumption else []}

    @classmethod
    def _result(cls, request, metric, formula, *, status, inputs=None, source_refs=None, assumptions=None, result=None, unit=None, scope=None, confidence_basis=None, evidence_refs=(), knowledge_refs=(), replay_inputs=None, missing_inputs=None, error_details=None):
        formula_id, formula_version = formula
        evidence = sorted(set(ref for ref in evidence_refs if ref)); knowledge = sorted(set(knowledge_refs))
        replay = {"schema_version": request.schema_version, "formula_id": formula_id, "formula_version": formula_version, "inputs": replay_inputs or {}, "knowledge_refs": knowledge, "evidence_refs": evidence}
        digest = sha256(json.dumps({"device_id": request.device_id, "metric": metric, "replay": replay}, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return LifetimeAssessmentResult(assessment_id=f"LIFE-{digest}", device_id=request.device_id, metric=metric, status=status, formula_id=formula_id, formula_version=formula_version, inputs=inputs or {}, assumptions=assumptions or [], result=result, unit=unit, scope=scope, confidence_basis=confidence_basis or [], evidence_refs=evidence, source_refs=sorted(set(source_refs or [])), knowledge_refs=knowledge, replay_trace=replay, missing_inputs=missing_inputs or [], error_details=error_details or [])

    def assess(self, request, metric):
        formula = self.registry.resolve(metric)
        try: return getattr(self, f"_assess_{metric.replace('.', '_')}")(request, metric, formula)
        except _InsufficientData as error: return self._result(request, metric, formula, status=LifetimeAssessmentStatus.INSUFFICIENT_DATA, missing_inputs=error.missing)
        except _InvalidInput as error: return self._result(request, metric, formula, status=LifetimeAssessmentStatus.INVALID_INPUT, error_details=error.details)

    def _assess_ssd_tbw(self, request, metric, formula):
        host, host_fact = self._fact(request, "host_written_bytes"); host_obs = None
        if host is None: host, host_obs = self._observation(request, "host_written_bytes")
        rated, rated_fact = self._fact(request, "rated_tbw_bytes")
        missing = [x for x, value in (("host_written_bytes", host), ("rated_tbw_bytes", rated)) if value is None]
        if missing: raise _InsufficientData(missing)
        if host_fact: self._unit(host_fact.unit, {"bytes"}, "host_written_bytes")
        if rated_fact: self._unit(rated_fact.unit, {"bytes"}, "rated_tbw_bytes")
        hn, rn = self._number(host, "host_written_bytes"), self._number(rated, "rated_tbw_bytes")
        if rn <= 0 or hn < 0: raise _InvalidInput(["TBW_INPUT_OUT_OF_RANGE"])
        refs = self._fact_refs(host_fact) + self._fact_refs(rated_fact) + self._obs_refs(host_obs); trace = {"host_written_bytes": self._trace("host_written_bytes", host, host_fact, host_obs), "rated_tbw_bytes": self._trace("rated_tbw_bytes", rated, rated_fact)}
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"host_written_bytes": hn, "rated_tbw_bytes": rn}, result={"consumed_ratio": hn / rn, "remaining_bytes": rn - hn}, unit="bytes_and_ratio", scope=rated_fact.scope if rated_fact else None, evidence_refs=refs, source_refs=[x["source_id"] for x in trace.values() if x.get("source_id")], replay_inputs=trace, confidence_basis=["DETERMINISTIC_ARITHMETIC"])

    def _assess_ssd_dwpd(self, request, metric, formula):
        host, host_fact = self._fact(request, "host_written_bytes"); host_obs = None
        if host is None: host, host_obs = self._observation(request, "host_written_bytes")
        capacity, capacity_fact = self._fact(request, "capacity_bytes"); days, assumption = self._assumption(request, "time_window_days", "elapsed_days")
        missing = [x for x, value in (("host_written_bytes", host), ("capacity_bytes", capacity), ("time_window_days", days)) if value is None]
        if missing: raise _InsufficientData(missing)
        for obj, name in ((host_fact, "host_written_bytes"), (capacity_fact, "capacity_bytes")):
            if obj: self._unit(obj.unit, {"bytes"}, name)
        hn, cn, dn = self._number(host, "host_written_bytes"), self._number(capacity, "capacity_bytes"), self._number(days, "time_window_days")
        if cn <= 0 or dn <= 0 or hn < 0: raise _InvalidInput(["DWPD_INPUT_OUT_OF_RANGE"])
        refs = self._fact_refs(host_fact) + self._fact_refs(capacity_fact) + self._obs_refs(host_obs) + list(assumption.evidence_refs if assumption else []); trace = {"host_written_bytes": self._trace("host_written_bytes", host, host_fact, host_obs), "capacity_bytes": self._trace("capacity_bytes", capacity, capacity_fact), "time_window_days": self._trace("time_window_days", days, assumption=assumption)}
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"host_written_bytes": hn, "capacity_bytes": cn, "time_window_days": dn}, assumptions=[assumption] if assumption else [], result=hn / cn / dn, unit="drive_writes_per_day", evidence_refs=refs, source_refs=[x["source_id"] for x in trace.values() if x.get("source_id")], replay_inputs=trace, confidence_basis=["DETERMINISTIC_ARITHMETIC", "TIME_WINDOW_ASSUMPTION"])

    def _protocol_observation(self, request, name, units):
        knowledge = self._require_knowledge(request, name); value, observation = self._observation(request, name)
        if value is None or observation is None: raise _InsufficientData([f"{name}:RUNTIME_OBSERVATION_REQUIRED"])
        self._unit(observation.unit, units, name); return value, observation, knowledge

    def _assess_nvme_data_units_written(self, request, metric, formula):
        value, observation, knowledge = self._protocol_observation(request, "data_units_written", {"data_units"}); factor = next((item.parameters.get("bytes_per_data_unit") for item in knowledge if item.parameters.get("bytes_per_data_unit") is not None), None)
        if factor is None: raise _InsufficientData(["bytes_per_data_unit:FORMAL_KNOWLEDGE_PARAMETER_REQUIRED"])
        factor_number, number = self._number(factor, "bytes_per_data_unit"), self._number(value, "data_units_written")
        if factor_number <= 0: raise _InvalidInput(["bytes_per_data_unit:POSITIVE_REQUIRED"])
        if number < 0: raise _InvalidInput(["data_units_written:NON_NEGATIVE_REQUIRED"])
        krefs = [item.knowledge_id for item in knowledge]; erefs = self._obs_refs(observation) + [ref for item in knowledge for ref in item.evidence_refs]; trace = {"data_units_written": self._trace("data_units_written", value, observation=observation), "bytes_per_data_unit": {"source_type": "FORMAL_KNOWLEDGE_PARAMETER", "value": factor_number, "knowledge_refs": krefs}}
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"data_units_written": number, "bytes_per_data_unit": factor_number}, result=number * factor_number, unit="bytes", scope="NVMe protocol-defined Data Units Written", evidence_refs=erefs, source_refs=[observation.observation_id], knowledge_refs=krefs, replay_inputs=trace, confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "EXPLICIT_PROTOCOL_FACTOR", "NORMALIZED_RUNTIME_OBSERVATION"])

    def _assess_nvme_percentage_used(self, request, metric, formula):
        value, observation, knowledge = self._protocol_observation(request, "percentage_used", {"%", "percent"}); number = self._number(value, "percentage_used")
        if number < 0: raise _InvalidInput(["PERCENTAGE_USED_NEGATIVE"])
        krefs = [item.knowledge_id for item in knowledge]; erefs = self._obs_refs(observation) + [ref for item in knowledge for ref in item.evidence_refs]
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"percentage_used": number}, result=number, unit="%", scope="NVMe protocol-defined Percentage Used", evidence_refs=erefs, source_refs=[observation.observation_id], knowledge_refs=krefs, replay_inputs={"percentage_used": self._trace("percentage_used", value, observation=observation)}, confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "NORMALIZED_RUNTIME_OBSERVATION"])

    def _assess_emmc_field(self, request, metric, formula, observation_name, parameter):
        value, observation, knowledge = self._protocol_observation(request, observation_name, {"tier", "hex", "code"}); mapping = next((item.parameters.get(parameter) for item in knowledge if item.parameters.get(parameter)), None)
        if mapping is None: raise _InsufficientData([f"{parameter}:FORMAL_KNOWLEDGE_PARAMETER_REQUIRED"])
        if not isinstance(mapping, dict): raise _InvalidInput([f"{parameter}:MAPPING_MUST_BE_OBJECT"])
        interpretation = mapping.get(str(value), "UNKNOWN"); krefs = [item.knowledge_id for item in knowledge]; erefs = self._obs_refs(observation) + [ref for item in knowledge for ref in item.evidence_refs]
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={observation_name: value, parameter: mapping}, result={"raw_tier": value, "interpretation": interpretation, "semantic_scope": metric}, unit="protocol_tier", scope="eMMC EXT_CSD protocol semantics", evidence_refs=erefs, source_refs=[observation.observation_id], knowledge_refs=krefs, replay_inputs={observation_name: self._trace(observation_name, value, observation=observation), parameter: {"source_type": "FORMAL_KNOWLEDGE_PARAMETER", "knowledge_refs": krefs, "value": mapping}}, confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "EXPLICIT_PROTOCOL_SEMANTICS", "NORMALIZED_RUNTIME_OBSERVATION"])

    def _assess_emmc_device_life_time_a(self, request, metric, formula): return self._assess_emmc_field(request, metric, formula, "device_life_time_a", "life_time_a_map")
    def _assess_emmc_device_life_time_b(self, request, metric, formula): return self._assess_emmc_field(request, metric, formula, "device_life_time_b", "life_time_b_map")
    def _assess_emmc_pre_eol_info(self, request, metric, formula): return self._assess_emmc_field(request, metric, formula, "pre_eol_info", "pre_eol_map")

    def _assess_emmc_life_time(self, request, metric, formula):
        children = [self.assess(request, name) for name in ("emmc.device_life_time_a", "emmc.device_life_time_b", "emmc.pre_eol_info")]
        if any(item.status is not LifetimeAssessmentStatus.CALCULATED for item in children): raise _InsufficientData([f"{item.metric}:{value}" for item in children for value in (item.missing_inputs or item.error_details)])
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"fields": [item.inputs for item in children]}, result={"life_time_a": children[0].result["interpretation"], "life_time_b": children[1].result["interpretation"], "pre_eol_info": children[2].result["interpretation"], "field_details": {"device_life_time_a": children[0].result, "device_life_time_b": children[1].result, "pre_eol_info": children[2].result}}, unit="protocol_tier", scope="eMMC EXT_CSD protocol semantics", evidence_refs=[ref for item in children for ref in item.evidence_refs], source_refs=[ref for item in children for ref in item.source_refs], knowledge_refs=[ref for item in children for ref in item.knowledge_refs], replay_inputs={"children": [item.replay_trace for item in children]}, confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "INDEPENDENT_EMMC_FIELD_SEMANTICS"])

    def _assess_nand_pe_margin(self, request, metric, formula):
        rated, rated_fact = self._fact(request, "rated_pe_cycles", "rated_endurance_cycles"); observed, observation = self._observation(request, "pe_cycle", "erase_count"); observed_fact = None
        if observed is None: observed, observed_fact = self._fact(request, "pe_cycle", "erase_count")
        if rated is None or observed is None: raise _InsufficientData([x for x, value in (("rated_pe_cycles", rated), ("pe_cycle_or_erase_count", observed)) if value is None])
        if rated_fact: self._unit(rated_fact.unit, {"cycles"}, "rated_pe_cycles")
        if observation: self._unit(observation.unit, {"cycles"}, "pe_cycle")
        rn, on = self._number(rated, "rated_pe_cycles"), self._number(observed, "observed_pe_cycles")
        if rn <= 0 or on < 0: raise _InvalidInput(["PE_INPUT_OUT_OF_RANGE"])
        refs = self._fact_refs(rated_fact) + self._fact_refs(observed_fact) + self._obs_refs(observation); sources = [x for x in ((rated_fact.fact_id if rated_fact else None), (observed_fact.fact_id if observed_fact else None), (observation.observation_id if observation else None)) if x]
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"rated_pe_cycles": rn, "observed_pe_cycles": on}, result=rn - on, unit="cycles", evidence_refs=refs, source_refs=sources, replay_inputs={"rated_pe_cycles": self._trace("rated_pe_cycles", rated, rated_fact), "observed_pe_cycles": self._trace("observed_pe_cycles", observed, observed_fact, observation)}, confidence_basis=["DETERMINISTIC_ARITHMETIC", "NEGATIVE_MARGIN_PRESERVED"])

    def _assess_nand_wear_distribution(self, request, metric, formula):
        values, observation = self._observation(request, "wear_distribution")
        if values is None or observation is None: raise _InsufficientData(["wear_distribution:RUNTIME_OBSERVATION_REQUIRED"])
        if isinstance(values, dict): values = list(values.values())
        if not isinstance(values, list) or not values: raise _InvalidInput(["wear_distribution:NON_EMPTY_NUMERIC_LIST_REQUIRED"])
        numbers = [self._number(value, "wear_distribution") for value in values]
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"wear_distribution": numbers}, result={"min": min(numbers), "max": max(numbers), "mean": fmean(numbers), "spread": max(numbers) - min(numbers)}, unit="cycles", evidence_refs=self._obs_refs(observation), source_refs=[observation.observation_id], replay_inputs={"wear_distribution": self._trace("wear_distribution", numbers, observation=observation)}, confidence_basis=["DETERMINISTIC_STATISTICS", "NORMALIZED_RUNTIME_OBSERVATION"])

    def _assess_generic_waf(self, request, metric, formula):
        media, media_fact = self._fact(request, "media_written_bytes"); host, host_fact = self._fact(request, "host_written_bytes")
        if media is None or host is None: raise _InsufficientData([x for x, value in (("media_written_bytes", media), ("host_written_bytes", host)) if value is None])
        self._unit(media_fact.unit, {"bytes"}, "media_written_bytes"); self._unit(host_fact.unit, {"bytes"}, "host_written_bytes")
        mn, hn = self._number(media, "media_written_bytes"), self._number(host, "host_written_bytes")
        if mn < 0 or hn <= 0: raise _InvalidInput(["WAF_INPUT_OUT_OF_RANGE"])
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"media_written_bytes": mn, "host_written_bytes": hn}, result=mn / hn, unit="ratio", evidence_refs=self._fact_refs(media_fact) + self._fact_refs(host_fact), source_refs=[media_fact.fact_id, host_fact.fact_id], replay_inputs={"media_written_bytes": self._trace("media_written_bytes", media, media_fact), "host_written_bytes": self._trace("host_written_bytes", host, host_fact)}, confidence_basis=["DETERMINISTIC_ARITHMETIC"])


class LifetimeAssessmentRepository:
    def __init__(self): self._items = {}
    def save(self, result): self._items[result.assessment_id] = result; return result
    def get(self, assessment_id): return self._items.get(assessment_id)


def create_lifetime_router(engine=None, repository=None):
    router = APIRouter(); engine = engine or LifetimeEngine(); repository = repository or LifetimeAssessmentRepository()
    @router.post("/storage/lifetime/assess", status_code=201)
    def assess(payload: dict[str, Any]):
        payload = dict(payload); metric = payload.pop("metric", None)
        if not metric: raise HTTPException(400, "METRIC_REQUIRED")
        try: result = engine.assess(LifetimeAssessmentRequest(**payload), metric)
        except ValueError as error: raise HTTPException(400, str(error)) from error
        return repository.save(result).model_dump(mode="json")
    @router.get("/storage/lifetime/assessments/{assessment_id}")
    def get_assessment(assessment_id: str):
        item = repository.get(assessment_id)
        if item is None: raise HTTPException(404, "LIFETIME_ASSESSMENT_NOT_FOUND")
        return item.model_dump(mode="json")
    @router.get("/storage/lifetime/formulas")
    def list_formulas(): return {"items": [spec.model_dump(mode="json") for spec in FormulaRegistry.SPECS.values()]}
    @router.get("/storage/lifetime/formulas/{formula_id:path}")
    def get_formula(formula_id: str):
        try: return FormulaRegistry.describe(formula_id).model_dump(mode="json")
        except ValueError as error: raise HTTPException(404, str(error)) from error
    return router
