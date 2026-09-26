"""Deterministic Storage lifetime domain contract (T2)."""

from __future__ import annotations

from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from statistics import fmean
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import Field

from runtime.contracts import ContractModel, RuntimeObservation


class LifetimeAssessmentStatus(str, Enum):
    CALCULATED = "CALCULATED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INVALID_INPUT = "INVALID_INPUT"


class ResultKind(str, Enum):
    NUMERIC = "NUMERIC"
    RANGE = "RANGE"
    ENUM_INTERPRETATION = "ENUM_INTERPRETATION"


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
    schema_version: str = "1.2"


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
    result_kind: ResultKind
    boundary_checks: list[str] = Field(default_factory=list)
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
    """Formal IDs are frozen; lower-case names are compatibility aliases only."""

    SPECS = {
        "SSD_TBW_CONSUMPTION_V1": FormulaSpec(formula_id="SSD_TBW_CONSUMPTION_V1", formula_version="1.0", description="Host written bytes against rated TBW", output_unit="bytes_and_ratio", required_inputs=["host_written_bytes", "rated_tbw_bytes"]),
        "SSD_DWPD_OBSERVED_V1": FormulaSpec(formula_id="SSD_DWPD_OBSERVED_V1", formula_version="1.0", description="Observed drive writes per day", output_unit="drive_writes_per_day", required_inputs=["host_written_bytes", "capacity_bytes", "time_window_days"]),
        "NVME_DATA_UNITS_WRITTEN_V1": FormulaSpec(formula_id="NVME_DATA_UNITS_WRITTEN_V1", formula_version="1.0", description="NVMe data units multiplied by released bytes-per-data-unit", output_unit="bytes", required_inputs=["data_units_written"], required_knowledge_parameters=["bytes_per_data_unit"], protocol_semantics=True),
        "NVME_PERCENTAGE_USED_INTERPRETATION_V1": FormulaSpec(formula_id="NVME_PERCENTAGE_USED_INTERPRETATION_V1", formula_version="1.0", description="NVMe Percentage Used interpretation only", output_unit="%", required_inputs=["percentage_used"], protocol_semantics=True),
        "EMMC_DEVICE_LIFE_TIME_A_V1": FormulaSpec(formula_id="EMMC_DEVICE_LIFE_TIME_A_V1", formula_version="1.0", description="eMMC DEVICE_LIFE_TIME_EST_TYP_A", output_unit="protocol_tier", required_inputs=["device_life_time_a"], required_knowledge_parameters=["life_time_a_map"], protocol_semantics=True),
        "EMMC_DEVICE_LIFE_TIME_B_V1": FormulaSpec(formula_id="EMMC_DEVICE_LIFE_TIME_B_V1", formula_version="1.0", description="eMMC DEVICE_LIFE_TIME_EST_TYP_B", output_unit="protocol_tier", required_inputs=["device_life_time_b"], required_knowledge_parameters=["life_time_b_map"], protocol_semantics=True),
        "EMMC_PRE_EOL_V1": FormulaSpec(formula_id="EMMC_PRE_EOL_V1", formula_version="1.0", description="eMMC PRE_EOL_INFO lifecycle warning", output_unit="protocol_tier", required_inputs=["pre_eol_info"], required_knowledge_parameters=["pre_eol_map"], protocol_semantics=True),
        "NAND_PE_MARGIN_V1": FormulaSpec(formula_id="NAND_PE_MARGIN_V1", formula_version="1.0", description="Rated P/E cycles minus observed P/E cycles", output_unit="cycles", required_inputs=["rated_pe_cycles", "pe_cycle"]),
        "NAND_ERASE_COUNT_MARGIN_V1": FormulaSpec(formula_id="NAND_ERASE_COUNT_MARGIN_V1", formula_version="1.0", description="Rated endurance minus observed erase count", output_unit="cycles", required_inputs=["rated_pe_cycles", "erase_count"]),
        "GENERIC_WAF_V1": FormulaSpec(formula_id="GENERIC_WAF_V1", formula_version="1.0", description="Media written bytes divided by host written bytes", output_unit="ratio", required_inputs=["media_written_bytes", "host_written_bytes"]),
        "GENERIC_ENDURANCE_MARGIN_V1": FormulaSpec(formula_id="GENERIC_ENDURANCE_MARGIN_V1", formula_version="1.0", description="Rated endurance minus observed endurance", output_unit="cycles", required_inputs=["rated_endurance_cycles", "observed_endurance_cycles"]),
        "NAND_WEAR_DISTRIBUTION_V1": FormulaSpec(formula_id="NAND_WEAR_DISTRIBUTION_V1", formula_version="1.0", description="Deterministic wear distribution statistics", output_unit="cycles", required_inputs=["wear_distribution"]),
    }
    ALIASES = {
        "ssd.tbw": "SSD_TBW_CONSUMPTION_V1", "ssd.dwpd": "SSD_DWPD_OBSERVED_V1",
        "nvme.data_units_written": "NVME_DATA_UNITS_WRITTEN_V1", "nvme.percentage_used": "NVME_PERCENTAGE_USED_INTERPRETATION_V1",
        "emmc.device_life_time_a": "EMMC_DEVICE_LIFE_TIME_A_V1", "emmc.device_life_time_b": "EMMC_DEVICE_LIFE_TIME_B_V1",
        "emmc.pre_eol_info": "EMMC_PRE_EOL_V1", "emmc.life_time": "EMMC_DEVICE_LIFE_TIME_A_V1",
        "nand.pe_margin": "NAND_PE_MARGIN_V1", "nand.erase_count_margin": "NAND_ERASE_COUNT_MARGIN_V1",
        "generic.waf": "GENERIC_WAF_V1", "generic.endurance_margin": "GENERIC_ENDURANCE_MARGIN_V1",
        "nand.wear_distribution": "NAND_WEAR_DISTRIBUTION_V1",
    }

    @classmethod
    def canonicalize(cls, metric: str) -> str:
        return cls.ALIASES.get(metric, metric)

    @classmethod
    def resolve(cls, metric: str) -> tuple[str, str]:
        formal = cls.canonicalize(metric)
        try: return formal, cls.SPECS[formal].formula_version
        except KeyError as error: raise ValueError(f"FORMULA_NOT_REGISTERED:{metric}") from error

    @classmethod
    def describe(cls, metric: str) -> FormulaSpec:
        formal = cls.canonicalize(metric)
        try: return cls.SPECS[formal]
        except KeyError as error: raise ValueError(f"FORMULA_NOT_REGISTERED:{metric}") from error

    @classmethod
    def list(cls) -> dict[str, tuple[str, str]]:
        return {key: (value.formula_id, value.formula_version) for key, value in cls.SPECS.items()}


class _InsufficientData(Exception):
    def __init__(self, missing: list[str]): self.missing = missing


class _InvalidInput(Exception):
    def __init__(self, details: list[str]): self.details = details


class LifetimeEngine:
    BYTE_FACTORS = {"b": 1, "bytes": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4, "pb": 1000**5, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4, "pib": 1024**5}

    def __init__(self, registry: type[FormulaRegistry] = FormulaRegistry): self.registry = registry

    @staticmethod
    def _fact(request, *names):
        for fact in request.confirmed_facts:
            if fact.metric_name in names:
                if fact.source_type != "CONFIRMED_DEVICE_FACT": raise _InvalidInput([f"{fact.metric_name}:FACT_SOURCE_MUST_BE_CONFIRMED_DEVICE_FACT"])
                if not fact.evidence_refs: raise _InvalidInput([f"{fact.metric_name}:FACT_EVIDENCE_REQUIRED"])
                return fact.value, fact
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

    @classmethod
    def _normalize(cls, value: Any, unit: str | None, target: str, name: str) -> tuple[float, str]:
        if not unit: raise _InvalidInput([f"{name}:UNIT_REQUIRED"])
        u = unit.strip().lower().replace(" ", "_")
        number = cls._number(value, name)
        if target == "bytes":
            key = u.replace("_", "")
            if key not in cls.BYTE_FACTORS: raise _InvalidInput([f"{name}:UNIT_INCOMPATIBLE:{unit}"])
            return number * cls.BYTE_FACTORS[key], "bytes"
        allowed = {"cycles": {"cycle", "cycles"}, "days": {"day", "days"}, "percent": {"%", "percent", "percentage"}, "data_units": {"data_unit", "data_units", "du"}}
        if target not in allowed or u not in allowed[target]: raise _InvalidInput([f"{name}:UNIT_INCOMPATIBLE:{unit}"])
        return number, {"percent": "%", "data_units": "data_units"}.get(target, target)

    @staticmethod
    def _knowledge(request): return [item for item in request.formal_knowledge if item.release_status == "RELEASED" and item.evidence_refs]

    @classmethod
    def _require_knowledge(cls, request, metric):
        knowledge = cls._knowledge(request)
        if not knowledge: raise _InsufficientData([f"{metric}:FORMAL_KNOWLEDGE_RELEASE_REQUIRED"])
        return knowledge

    @staticmethod
    def _refs(fact=None, observation=None):
        if fact: return list(fact.evidence_refs)
        return [observation.evidence_ref] if observation and observation.evidence_ref else []

    @staticmethod
    def _trace(name, value, unit, fact=None, observation=None, assumption=None):
        if fact: return {"name": name, "source_type": "FACT", "source_ref": fact.fact_id, "value": value, "normalized_value": value, "normalized_unit": unit, "evidence_refs": list(fact.evidence_refs)}
        if observation: return {"name": name, "source_type": "RUNTIME", "source_ref": observation.observation_id, "value": value, "normalized_value": value, "normalized_unit": unit, "evidence_refs": [observation.evidence_ref] if observation.evidence_ref else [], "raw_output_ref": observation.raw_output_ref, "capture_time": observation.capture_time.isoformat() if observation.capture_time else None}
        return {"name": name, "source_type": "ASSUMPTION", "source_ref": assumption.name if assumption else None, "value": value, "normalized_value": value, "normalized_unit": unit, "evidence_refs": list(assumption.evidence_refs) if assumption else []}

    @classmethod
    def _result(cls, request, metric, formula, *, status, result_kind=ResultKind.NUMERIC, boundary_checks=None, inputs=None, source_refs=None, assumptions=None, result=None, unit=None, scope=None, confidence_basis=None, evidence_refs=(), knowledge_refs=(), replay_inputs=None, missing_inputs=None, error_details=None):
        formula_id, formula_version = formula; evidence = sorted(set(ref for ref in evidence_refs if ref)); knowledge = sorted(set(knowledge_refs))
        replay = {"schema_version": request.schema_version, "formula_id": formula_id, "formula_version": formula_version, "inputs": replay_inputs or {}, "knowledge_refs": knowledge, "evidence_refs": evidence}
        digest = sha256(json.dumps({"device_id": request.device_id, "metric": metric, "replay": replay}, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return LifetimeAssessmentResult(assessment_id=f"LIFE-{digest}", device_id=request.device_id, metric=metric, status=status, formula_id=formula_id, formula_version=formula_version, result_kind=result_kind, boundary_checks=boundary_checks or [], inputs=inputs or {}, assumptions=assumptions or [], result=result, unit=unit, scope=scope, confidence_basis=confidence_basis or [], evidence_refs=evidence, source_refs=sorted(set(source_refs or [])), knowledge_refs=knowledge, replay_trace=replay, missing_inputs=missing_inputs or [], error_details=error_details or [])

    def assess(self, request, metric):
        original = metric; formal, _ = self.registry.resolve(metric)
        methods = {"SSD_TBW_CONSUMPTION_V1": "_assess_ssd_tbw", "SSD_DWPD_OBSERVED_V1": "_assess_ssd_dwpd", "NVME_DATA_UNITS_WRITTEN_V1": "_assess_nvme_data_units_written", "NVME_PERCENTAGE_USED_INTERPRETATION_V1": "_assess_nvme_percentage_used", "EMMC_DEVICE_LIFE_TIME_A_V1": "_assess_emmc_device_life_time_a", "EMMC_DEVICE_LIFE_TIME_B_V1": "_assess_emmc_device_life_time_b", "EMMC_PRE_EOL_V1": "_assess_emmc_pre_eol_info", "NAND_PE_MARGIN_V1": "_assess_nand_pe_margin", "NAND_ERASE_COUNT_MARGIN_V1": "_assess_nand_erase_count_margin", "GENERIC_WAF_V1": "_assess_generic_waf", "GENERIC_ENDURANCE_MARGIN_V1": "_assess_generic_endurance_margin", "NAND_WEAR_DISTRIBUTION_V1": "_assess_nand_wear_distribution"}
        formula = (formal, self.registry.SPECS[formal].formula_version)
        try:
            if original == "emmc.life_time": return self._assess_emmc_aggregate(request, original, formula)
            return getattr(self, methods[formal])(request, original, formula)
        except _InsufficientData as error: return self._result(request, original, formula, status=LifetimeAssessmentStatus.INSUFFICIENT_DATA, missing_inputs=error.missing)
        except _InvalidInput as error: return self._result(request, original, formula, status=LifetimeAssessmentStatus.INVALID_INPUT, error_details=error.details)

    def _numeric_input(self, request, names, target, label):
        value, fact = self._fact(request, *names); observation = None
        if value is None: value, observation = self._observation(request, *names)
        if value is None: raise _InsufficientData([label])
        normalized, unit = self._normalize(value, fact.unit if fact else observation.unit, target, label)
        return normalized, unit, fact, observation

    def _assess_ssd_tbw(self, request, metric, formula):
        host, hu, hf, ho = self._numeric_input(request, ("host_written_bytes",), "bytes", "host_written_bytes"); rated, ru, rf, _ = self._numeric_input(request, ("rated_tbw_bytes",), "bytes", "rated_tbw_bytes")
        if rated <= 0 or host < 0: raise _InvalidInput(["TBW_INPUT_OUT_OF_RANGE"])
        trace = {"host_written_bytes": self._trace("host_written_bytes", host, hu, hf, ho), "rated_tbw_bytes": self._trace("rated_tbw_bytes", rated, ru, rf)}
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, result_kind=ResultKind.RANGE, inputs={"host_written_bytes": host, "rated_tbw_bytes": rated}, result={"consumed_ratio": host / rated, "remaining_bytes": rated - host}, unit="bytes_and_ratio", evidence_refs=self._refs(hf, ho) + self._refs(rf), source_refs=[x["source_ref"] for x in trace.values()], replay_inputs=trace, confidence_basis=["DETERMINISTIC_ARITHMETIC"])

    def _assess_ssd_dwpd(self, request, metric, formula):
        host, hu, hf, ho = self._numeric_input(request, ("host_written_bytes",), "bytes", "host_written_bytes"); capacity, cu, cf, _ = self._numeric_input(request, ("capacity_bytes",), "bytes", "capacity_bytes"); days, assumption = self._assumption(request, "time_window_days", "elapsed_days")
        if days is None: raise _InsufficientData(["time_window_days"])
        days, du = self._normalize(days, assumption.unit, "days", "time_window_days")
        if capacity <= 0 or days <= 0 or host < 0: raise _InvalidInput(["DWPD_INPUT_OUT_OF_RANGE"])
        trace = {"host_written_bytes": self._trace("host_written_bytes", host, hu, hf, ho), "capacity_bytes": self._trace("capacity_bytes", capacity, cu, cf), "time_window_days": self._trace("time_window_days", days, du, assumption=assumption)}
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"host_written_bytes": host, "capacity_bytes": capacity, "time_window_days": days}, assumptions=[assumption], result=host / capacity / days, unit="drive_writes_per_day", evidence_refs=self._refs(hf, ho) + self._refs(cf) + assumption.evidence_refs, source_refs=[x["source_ref"] for x in trace.values()], replay_inputs=trace, confidence_basis=["DETERMINISTIC_ARITHMETIC", "TIME_WINDOW_ASSUMPTION"])

    def _protocol_observation(self, request, name, target):
        knowledge = self._require_knowledge(request, name); value, observation = self._observation(request, name)
        if value is None or observation is None: raise _InsufficientData([f"{name}:RUNTIME_OBSERVATION_REQUIRED"])
        if target == "tier":
            if observation.unit not in {"tier", "code", "hex"}: raise _InvalidInput([f"{name}:UNIT_INCOMPATIBLE:{observation.unit}"])
            return value, "protocol_tier", observation, knowledge
        if name == "wear_distribution":
            if observation.unit not in {"cycle", "cycles"}: raise _InvalidInput([f"{name}:UNIT_INCOMPATIBLE:{observation.unit}"])
            return value, "cycles", observation, knowledge
        normalized, unit = self._normalize(value, observation.unit, target, name); return normalized, unit, observation, knowledge

    def _knowledge_trace(self, knowledge, parameter, value):
        return {"source_type": "FORMAL_KNOWLEDGE_PARAMETER", "source_ref": next(item.knowledge_id for item in knowledge if item.parameters.get(parameter) is not None), "knowledge_ref": next(item.knowledge_id for item in knowledge if item.parameters.get(parameter) is not None), "value": value, "evidence_refs": [ref for item in knowledge for ref in item.evidence_refs]}

    def _assess_nvme_data_units_written(self, request, metric, formula):
        value, unit, obs, knowledge = self._protocol_observation(request, "data_units_written", "data_units"); raw_factor = next((item.parameters.get("bytes_per_data_unit") for item in knowledge if item.parameters.get("bytes_per_data_unit") is not None), None)
        if raw_factor is None: raise _InsufficientData(["bytes_per_data_unit:FORMAL_KNOWLEDGE_PARAMETER_REQUIRED"])
        factor = raw_factor.get("value") if isinstance(raw_factor, dict) else raw_factor; factor, _ = self._normalize(factor, raw_factor.get("unit", "bytes") if isinstance(raw_factor, dict) else "bytes", "bytes", "bytes_per_data_unit")
        if factor <= 0 or value < 0: raise _InvalidInput(["NVME_DUW_INPUT_OUT_OF_RANGE"])
        krefs = [item.knowledge_id for item in knowledge]; trace = {"data_units_written": self._trace("data_units_written", value, unit, observation=obs), "bytes_per_data_unit": self._knowledge_trace(knowledge, "bytes_per_data_unit", factor)}
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"data_units_written": value, "bytes_per_data_unit": factor}, result=value * factor, unit="bytes", scope="NVMe protocol Data Units Written", evidence_refs=self._refs(observation=obs) + [ref for item in knowledge for ref in item.evidence_refs], source_refs=[obs.observation_id], knowledge_refs=krefs, replay_inputs=trace, confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "EXPLICIT_PROTOCOL_FACTOR"])

    def _assess_nvme_percentage_used(self, request, metric, formula):
        value, unit, obs, knowledge = self._protocol_observation(request, "percentage_used", "percent")
        if value < 0: raise _InvalidInput(["PERCENTAGE_USED_NEGATIVE"])
        krefs = [item.knowledge_id for item in knowledge]
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"percentage_used": value}, result=value, unit="%", scope="NVMe Percentage Used interpretation only", evidence_refs=self._refs(observation=obs) + [ref for item in knowledge for ref in item.evidence_refs], source_refs=[obs.observation_id], knowledge_refs=krefs, replay_inputs={"percentage_used": self._trace("percentage_used", value, unit, observation=obs)}, confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "NO_REMAINING_LIFE_INFERENCE"])

    def _assess_emmc_field(self, request, metric, formula, name, parameter):
        value, unit, obs, knowledge = self._protocol_observation(request, name, "tier"); mapping = next((item.parameters.get(parameter) for item in knowledge if item.parameters.get(parameter)), None)
        if not isinstance(mapping, dict): raise _InsufficientData([f"{parameter}:FORMAL_KNOWLEDGE_PARAMETER_REQUIRED"])
        krefs = [item.knowledge_id for item in knowledge]; return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, result_kind=ResultKind.ENUM_INTERPRETATION, inputs={name: value}, result={"raw_tier": value, "interpretation": mapping.get(str(value), "UNKNOWN"), "semantic_scope": metric}, unit="protocol_tier", scope="eMMC EXT_CSD protocol semantics", evidence_refs=self._refs(observation=obs) + [ref for item in knowledge for ref in item.evidence_refs], source_refs=[obs.observation_id], knowledge_refs=krefs, replay_inputs={name: self._trace(name, value, unit, observation=obs), parameter: self._knowledge_trace(knowledge, parameter, mapping)}, confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "EXPLICIT_PROTOCOL_SEMANTICS"])

    def _assess_emmc_device_life_time_a(self, request, metric, formula): return self._assess_emmc_field(request, metric, formula, "device_life_time_a", "life_time_a_map")
    def _assess_emmc_device_life_time_b(self, request, metric, formula): return self._assess_emmc_field(request, metric, formula, "device_life_time_b", "life_time_b_map")
    def _assess_emmc_pre_eol_info(self, request, metric, formula): return self._assess_emmc_field(request, metric, formula, "pre_eol_info", "pre_eol_map")

    def _assess_emmc_aggregate(self, request, metric, formula):
        children = [self.assess(request, name) for name in ("emmc.device_life_time_a", "emmc.device_life_time_b", "emmc.pre_eol_info")]
        if any(item.status is not LifetimeAssessmentStatus.CALCULATED for item in children):
            raise _InsufficientData([f"{item.metric}:{value}" for item in children for value in (item.missing_inputs or item.error_details)])
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, result_kind=ResultKind.ENUM_INTERPRETATION, result={"life_time_a": children[0].result["interpretation"], "life_time_b": children[1].result["interpretation"], "pre_eol_info": children[2].result["interpretation"]}, unit="protocol_tier", evidence_refs=[ref for item in children for ref in item.evidence_refs], source_refs=[ref for item in children for ref in item.source_refs], knowledge_refs=[ref for item in children for ref in item.knowledge_refs], replay_inputs={"children": [item.replay_trace for item in children]}, confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "INDEPENDENT_EMMC_FIELD_SEMANTICS"])

    def _assess_nand_margin(self, request, metric, formula, rated_name, observed_names):
        rated, ru, rf, _ = self._numeric_input(request, (rated_name,), "cycles", rated_name); observed, ou, of, oo = self._numeric_input(request, observed_names, "cycles", observed_names[0])
        if rated <= 0 or observed < 0: raise _InvalidInput(["MARGIN_INPUT_OUT_OF_RANGE"])
        boundary = ["BELOW_ZERO_MARGIN"] if rated - observed < 0 else []; trace = {rated_name: self._trace(rated_name, rated, ru, rf), observed_names[0]: self._trace(observed_names[0], observed, ou, of, oo)}
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={rated_name: rated, observed_names[0]: observed}, result=rated - observed, unit="cycles", boundary_checks=boundary, evidence_refs=self._refs(rf) + self._refs(of, oo), source_refs=[x["source_ref"] for x in trace.values()], replay_inputs=trace, confidence_basis=["DETERMINISTIC_ARITHMETIC", "NEGATIVE_MARGIN_PRESERVED"])

    def _assess_nand_pe_margin(self, request, metric, formula): return self._assess_nand_margin(request, metric, formula, "rated_pe_cycles", ("pe_cycle", "erase_count"))
    def _assess_nand_erase_count_margin(self, request, metric, formula): return self._assess_nand_margin(request, metric, formula, "rated_pe_cycles", ("erase_count",))
    def _assess_generic_endurance_margin(self, request, metric, formula): return self._assess_nand_margin(request, metric, formula, "rated_endurance_cycles", ("observed_endurance_cycles", "erase_count"))

    def _assess_nand_wear_distribution(self, request, metric, formula):
        values, obs = self._observation(request, "wear_distribution")
        if values is None or obs is None: raise _InsufficientData(["wear_distribution:RUNTIME_OBSERVATION_REQUIRED"])
        if obs.unit not in {"cycle", "cycles"}: raise _InvalidInput([f"wear_distribution:UNIT_INCOMPATIBLE:{obs.unit}"])
        unit = "cycles"
        if not isinstance(values, list) or not values: raise _InvalidInput(["wear_distribution:NON_EMPTY_NUMERIC_LIST_REQUIRED"])
        numbers = [self._number(value, "wear_distribution") for value in values]
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED, inputs={"wear_distribution": numbers}, result={"min": min(numbers), "max": max(numbers), "mean": fmean(numbers), "spread": max(numbers) - min(numbers)}, unit="cycles", evidence_refs=self._refs(observation=obs), source_refs=[obs.observation_id], replay_inputs={"wear_distribution": self._trace("wear_distribution", numbers, unit, observation=obs)}, confidence_basis=["DETERMINISTIC_STATISTICS"])

    def _assess_generic_waf(self, request, metric, formula):
        media, mu, mf, _ = self._numeric_input(request, ("media_written_bytes",), "bytes", "media_written_bytes"); host, hu, hf, _ = self._numeric_input(request, ("host_written_bytes",), "bytes", "host_written_bytes")
        if media < 0 or host <= 0: raise _InvalidInput(["WAF_INPUT_OUT_OF_RANGE"])
        trace = {"media_written_bytes": self._trace("media_written_bytes", media, mu, mf), "host_written_bytes": self._trace("host_written_bytes", host, hu, hf)}
        return self._result(request, metric, formula, inputs={"media_written_bytes": media, "host_written_bytes": host}, result=media / host, unit="ratio", evidence_refs=self._refs(mf) + self._refs(hf), source_refs=[x["source_ref"] for x in trace.values()], replay_inputs=trace, confidence_basis=["DETERMINISTIC_ARITHMETIC"])


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
