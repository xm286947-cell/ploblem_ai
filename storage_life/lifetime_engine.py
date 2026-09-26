"""Deterministic Storage lifetime calculations.

The engine consumes confirmed facts and formally captured runtime
observations.  It has no AI/provider dependency and never performs implicit
protocol conversion.  Protocol semantics must be supplied by a released
Formal Knowledge reference.
"""

from __future__ import annotations

from enum import Enum
from math import isfinite
from statistics import fmean
from typing import Any, Iterable

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


class LifetimeAssessmentRequest(ContractModel):
    device_id: str
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list)
    runtime_observations: list[RuntimeObservation] = Field(default_factory=list)
    formal_knowledge: list[FormalKnowledgeReference] = Field(default_factory=list)
    assumptions: list[LifetimeAssumption] = Field(default_factory=list)


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
    missing_inputs: list[str] = Field(default_factory=list)
    error_details: list[str] = Field(default_factory=list)


class FormulaRegistry:
    """Versioned registry with no hidden conversion constants."""

    FORMULAS = {
        "ssd.tbw": ("ssd.tbw", "1.0"),
        "ssd.dwpd": ("ssd.dwpd", "1.0"),
        "nvme.data_units_written": ("nvme.data_units_written", "1.0"),
        "nvme.percentage_used": ("nvme.percentage_used", "1.0"),
        "emmc.life_time": ("emmc.life_time", "1.0"),
        "nand.pe_margin": ("nand.pe_margin", "1.0"),
        "nand.wear_distribution": ("nand.wear_distribution", "1.0"),
        "generic.waf": ("generic.waf", "1.0"),
    }

    @classmethod
    def resolve(cls, metric: str) -> tuple[str, str]:
        try:
            return cls.FORMULAS[metric]
        except KeyError as error:
            raise ValueError(f"FORMULA_NOT_REGISTERED:{metric}") from error

    @classmethod
    def list(cls) -> dict[str, tuple[str, str]]:
        return dict(cls.FORMULAS)


class _InsufficientData(Exception):
    def __init__(self, missing: list[str]):
        self.missing = missing


class _InvalidInput(Exception):
    def __init__(self, details: list[str]):
        self.details = details


class LifetimeEngine:
    """Pure deterministic assessment engine."""

    PROTOCOL_SEMANTIC_METRICS = {
        "nvme.data_units_written",
        "nvme.percentage_used",
        "emmc.life_time",
    }

    def __init__(self, registry: type[FormulaRegistry] = FormulaRegistry):
        self.registry = registry

    @staticmethod
    def _fact(request: LifetimeAssessmentRequest, *names: str) -> tuple[Any, ConfirmedFact | None]:
        for fact in request.confirmed_facts:
            if fact.metric_name in names:
                return fact.value, fact
        return None, None

    @staticmethod
    def _observation(request: LifetimeAssessmentRequest, *names: str) -> tuple[Any, RuntimeObservation | None]:
        for observation in request.runtime_observations:
            if observation.metric_name in names and observation.is_formally_consumable:
                return observation.normalized_value, observation
        return None, None

    @staticmethod
    def _assumption(request: LifetimeAssessmentRequest, *names: str) -> tuple[Any, LifetimeAssumption | None]:
        for assumption in request.assumptions:
            if assumption.name in names:
                return assumption.value, assumption
        return None, None

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, bool):
            raise _InvalidInput([f"{name}:BOOLEAN_NOT_NUMERIC"])
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise _InvalidInput([f"{name}:NOT_NUMERIC"]) from error
        if not isfinite(number):
            raise _InvalidInput([f"{name}:NOT_FINITE"])
        return number

    @staticmethod
    def _knowledge(request: LifetimeAssessmentRequest) -> list[FormalKnowledgeReference]:
        return [
            item
            for item in request.formal_knowledge
            if item.release_status == "RELEASED" and item.evidence_refs
        ]

    @classmethod
    def _require_knowledge(cls, request: LifetimeAssessmentRequest, metric: str) -> list[FormalKnowledgeReference]:
        knowledge = cls._knowledge(request)
        if not knowledge:
            raise _InsufficientData([f"{metric}:FORMAL_KNOWLEDGE_RELEASE_REQUIRED"])
        return knowledge

    @staticmethod
    def _result(
        request: LifetimeAssessmentRequest,
        metric: str,
        formula: tuple[str, str],
        *,
        status: LifetimeAssessmentStatus,
        inputs: dict[str, Any] | None = None,
        assumptions: list[LifetimeAssumption] | None = None,
        result: Any = None,
        unit: str | None = None,
        scope: str | None = None,
        condition: str | None = None,
        confidence_basis: list[str] | None = None,
        evidence_refs: Iterable[str] = (),
        missing_inputs: list[str] | None = None,
        error_details: list[str] | None = None,
    ) -> LifetimeAssessmentResult:
        return LifetimeAssessmentResult(
            assessment_id=f"LIFE-{request.device_id}-{metric}",
            device_id=request.device_id,
            metric=metric,
            status=status,
            formula_id=formula[0],
            formula_version=formula[1],
            inputs=inputs or {},
            assumptions=assumptions or [],
            result=result,
            unit=unit,
            scope=scope,
            condition=condition,
            confidence_basis=confidence_basis or [],
            evidence_refs=sorted({ref for ref in evidence_refs if ref}),
            missing_inputs=missing_inputs or [],
            error_details=error_details or [],
        )

    def assess(self, request: LifetimeAssessmentRequest, metric: str) -> LifetimeAssessmentResult:
        formula = self.registry.resolve(metric)
        try:
            result = getattr(self, f"_assess_{metric.replace('.', '_')}")(request, metric, formula)
            return result
        except _InsufficientData as error:
            return self._result(
                request,
                metric,
                formula,
                status=LifetimeAssessmentStatus.INSUFFICIENT_DATA,
                missing_inputs=error.missing,
            )
        except _InvalidInput as error:
            return self._result(
                request,
                metric,
                formula,
                status=LifetimeAssessmentStatus.INVALID_INPUT,
                error_details=error.details,
            )

    def _assess_ssd_tbw(self, request, metric, formula):
        host, host_fact = self._fact(request, "host_written_bytes")
        if host is None:
            host, host_obs = self._observation(request, "host_written_bytes")
        else:
            host_obs = None
        rated, rated_fact = self._fact(request, "rated_tbw_bytes")
        if host is None or rated is None:
            raise _InsufficientData([x for x, value in (("host_written_bytes", host), ("rated_tbw_bytes", rated)) if value is None])
        host_number = self._number(host, "host_written_bytes")
        rated_number = self._number(rated, "rated_tbw_bytes")
        if rated_number <= 0 or host_number < 0:
            raise _InvalidInput(["TBW_INPUT_OUT_OF_RANGE"])
        evidence = (host_fact.evidence_refs if host_fact else []) + (rated_fact.evidence_refs if rated_fact else [])
        if host_obs:
            evidence += [host_obs.evidence_ref or ""]
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED,
            inputs={"host_written_bytes": host_number, "rated_tbw_bytes": rated_number},
            result={"consumed_ratio": host_number / rated_number, "remaining_bytes": max(rated_number - host_number, 0)},
            unit="bytes_and_ratio", scope=(rated_fact.scope if rated_fact else None), evidence_refs=evidence,
            confidence_basis=["DETERMINISTIC_ARITHMETIC"])

    def _assess_ssd_dwpd(self, request, metric, formula):
        host, host_fact = self._fact(request, "host_written_bytes")
        if host is None:
            host, host_obs = self._observation(request, "host_written_bytes")
        else:
            host_obs = None
        capacity, capacity_fact = self._fact(request, "capacity_bytes")
        days, assumption = self._assumption(request, "time_window_days", "elapsed_days")
        missing = [name for name, value in (("host_written_bytes", host), ("capacity_bytes", capacity), ("time_window_days", days)) if value is None]
        if missing:
            raise _InsufficientData(missing)
        host_number, capacity_number, days_number = (self._number(host, "host_written_bytes"), self._number(capacity, "capacity_bytes"), self._number(days, "time_window_days"))
        if capacity_number <= 0 or days_number <= 0 or host_number < 0:
            raise _InvalidInput(["DWPD_INPUT_OUT_OF_RANGE"])
        evidence = (host_fact.evidence_refs if host_fact else []) + (capacity_fact.evidence_refs if capacity_fact else [])
        if host_obs:
            evidence.append(host_obs.evidence_ref or "")
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED,
            inputs={"host_written_bytes": host_number, "capacity_bytes": capacity_number, "time_window_days": days_number},
            assumptions=[assumption] if assumption else [], result=host_number / capacity_number / days_number,
            unit="drive_writes_per_day", evidence_refs=evidence, confidence_basis=["DETERMINISTIC_ARITHMETIC", "TIME_WINDOW_ASSUMPTION"])

    def _assess_nvme_data_units_written(self, request, metric, formula):
        knowledge = self._require_knowledge(request, metric)
        value, observation = self._observation(request, "data_units_written")
        if value is None or observation is None:
            raise _InsufficientData(["data_units_written:RUNTIME_OBSERVATION_REQUIRED"])
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED,
            inputs={"data_units_written": value}, result=value, unit=observation.unit, scope="NVMe protocol-defined Data Units Written",
            evidence_refs=[observation.evidence_ref or ""] + [ref for item in knowledge for ref in item.evidence_refs],
            confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "NORMALIZED_RUNTIME_OBSERVATION"])

    def _assess_nvme_percentage_used(self, request, metric, formula):
        knowledge = self._require_knowledge(request, metric)
        value, observation = self._observation(request, "percentage_used")
        if value is None or observation is None:
            raise _InsufficientData(["percentage_used:RUNTIME_OBSERVATION_REQUIRED"])
        number = self._number(value, "percentage_used")
        if number < 0:
            raise _InvalidInput(["PERCENTAGE_USED_NEGATIVE"])
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED,
            inputs={"percentage_used": number}, result=number, unit=observation.unit or "%",
            scope="NVMe protocol-defined Percentage Used", evidence_refs=[observation.evidence_ref or ""] + [ref for item in knowledge for ref in item.evidence_refs],
            confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "NORMALIZED_RUNTIME_OBSERVATION"])

    def _assess_emmc_life_time(self, request, metric, formula):
        knowledge = self._require_knowledge(request, metric)
        life_a, obs_a = self._observation(request, "device_life_time_a")
        life_b, obs_b = self._observation(request, "device_life_time_b")
        pre_eol, obs_eol = self._observation(request, "pre_eol_info")
        mapping = next((item.parameters.get("life_time_map") for item in knowledge if item.parameters.get("life_time_map")), None)
        missing = [name for name, value in (("device_life_time_a", life_a), ("device_life_time_b", life_b), ("pre_eol_info", pre_eol), ("life_time_map", mapping)) if value is None]
        if missing:
            raise _InsufficientData(missing)
        evidence = [obs.evidence_ref or "" for obs in (obs_a, obs_b, obs_eol) if obs] + [ref for item in knowledge for ref in item.evidence_refs]
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED,
            inputs={"device_life_time_a": life_a, "device_life_time_b": life_b, "pre_eol_info": pre_eol},
            result={"life_time_a": mapping.get(str(life_a), "UNKNOWN"), "life_time_b": mapping.get(str(life_b), "UNKNOWN"), "pre_eol_info": pre_eol},
            unit="protocol_tier", scope="eMMC EXT_CSD protocol semantics", evidence_refs=evidence,
            confidence_basis=["FORMAL_KNOWLEDGE_RELEASE", "NORMALIZED_RUNTIME_OBSERVATION"])

    def _assess_nand_pe_margin(self, request, metric, formula):
        rated, rated_fact = self._fact(request, "rated_pe_cycles", "rated_endurance_cycles")
        observed, observation = self._observation(request, "pe_cycle", "erase_count")
        if observed is None:
            observed, observed_fact = self._fact(request, "pe_cycle", "erase_count")
        else:
            observed_fact = None
        missing = [name for name, value in (("rated_pe_cycles", rated), ("pe_cycle_or_erase_count", observed)) if value is None]
        if missing:
            raise _InsufficientData(missing)
        rated_number, observed_number = self._number(rated, "rated_pe_cycles"), self._number(observed, "pe_cycle")
        if rated_number <= 0 or observed_number < 0:
            raise _InvalidInput(["PE_INPUT_OUT_OF_RANGE"])
        evidence = (rated_fact.evidence_refs if rated_fact else []) + (observed_fact.evidence_refs if observed_fact else []) + ([observation.evidence_ref or ""] if observation else [])
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED,
            inputs={"rated_pe_cycles": rated_number, "observed_pe_cycles": observed_number}, result=max(rated_number - observed_number, 0), unit="cycles",
            scope=(rated_fact.scope if rated_fact else None), evidence_refs=evidence, confidence_basis=["DETERMINISTIC_ARITHMETIC"])

    def _assess_nand_wear_distribution(self, request, metric, formula):
        values, observation = self._observation(request, "wear_distribution")
        if values is None or observation is None:
            raise _InsufficientData(["wear_distribution:RUNTIME_OBSERVATION_REQUIRED"])
        if isinstance(values, dict):
            values = list(values.values())
        if not isinstance(values, list) or not values:
            raise _InvalidInput(["wear_distribution:NON_EMPTY_NUMERIC_LIST_REQUIRED"])
        numbers = [self._number(value, "wear_distribution") for value in values]
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED,
            inputs={"wear_distribution": numbers}, result={"min": min(numbers), "max": max(numbers), "mean": fmean(numbers), "spread": max(numbers) - min(numbers)},
            unit="cycles", evidence_refs=[observation.evidence_ref or ""], confidence_basis=["DETERMINISTIC_STATISTICS", "NORMALIZED_RUNTIME_OBSERVATION"])

    def _assess_generic_waf(self, request, metric, formula):
        media, media_fact = self._fact(request, "media_written_bytes")
        host, host_fact = self._fact(request, "host_written_bytes")
        if media is None:
            media, media_obs = self._observation(request, "media_written_bytes")
        else:
            media_obs = None
        if host is None:
            host, host_obs = self._observation(request, "host_written_bytes")
        else:
            host_obs = None
        if media is None or host is None:
            raise _InsufficientData([x for x, value in (("media_written_bytes", media), ("host_written_bytes", host)) if value is None])
        media_number, host_number = self._number(media, "media_written_bytes"), self._number(host, "host_written_bytes")
        if media_number < 0 or host_number <= 0:
            raise _InvalidInput(["WAF_INPUT_OUT_OF_RANGE"])
        evidence = (media_fact.evidence_refs if media_fact else []) + (host_fact.evidence_refs if host_fact else []) + ([media_obs.evidence_ref or ""] if media_obs else []) + ([host_obs.evidence_ref or ""] if host_obs else [])
        return self._result(request, metric, formula, status=LifetimeAssessmentStatus.CALCULATED,
            inputs={"media_written_bytes": media_number, "host_written_bytes": host_number}, result=media_number / host_number, unit="ratio", evidence_refs=evidence, confidence_basis=["DETERMINISTIC_ARITHMETIC"])
