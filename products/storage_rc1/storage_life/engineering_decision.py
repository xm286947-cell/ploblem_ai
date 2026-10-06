from __future__ import annotations

"""Thin NAND engineering-decision composition layer.

This module deliberately does not own a second knowledge store, Runtime, Skill
stack or lifetime engine.  It only turns explicit mission/workload inputs into a
small RequiredStorageProfile and projects already-confirmed Device Facts / existing
Skill results into role views.

Formal engineering conclusions remain gated by the existing Knowledge Release and
Evidence contracts.  Missing facts/knowledge stay UNKNOWN; UNKNOWN is never SAFE.
"""

from typing import Any
import math
import re

from .lifetime_engine import LifetimeAssumption, LifetimeAssessmentRequest, LifetimeEngine


FIT = "FIT"
FIT_WITH_RISK = "FIT_WITH_RISK"
NOT_FIT = "NOT_FIT"
UNKNOWN = "UNKNOWN"


def _positive_number(value: Any, name: str, *, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}:NUMBER_REQUIRED") from exc
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise ValueError(f"{name}:POSITIVE_NUMBER_REQUIRED")
    return number


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def derive_required_storage_profile(
    mission_profile: dict[str, Any] | None,
    workload_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive only the requirements that are supported by explicit inputs.

    For Raw NAND the first deterministic system-level bridge is:
        required P/E >= P/E-per-day * service years * operating-days/year
                      * (1 + explicit design margin)

    We intentionally do not infer P/E-per-day from logical writes unless an
    upstream model has already established that stress metric.  That avoids
    silently inventing WAF / wear-leveling / over-provisioning assumptions.
    """

    mission = dict(mission_profile or {})
    workload = dict(workload_profile or {})
    missing: list[str] = []
    assumptions: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []

    years_raw = mission.get("target_service_life_years")
    if years_raw in (None, ""):
        years = None
        missing.append("TARGET_SERVICE_LIFE_YEARS_REQUIRED")
    else:
        years = _positive_number(years_raw, "target_service_life_years")

    days_raw = mission.get("operating_days_per_year", 365)
    days_per_year = _positive_number(days_raw, "operating_days_per_year")
    if "operating_days_per_year" not in mission:
        assumptions.append({
            "name": "operating_days_per_year",
            "value": 365,
            "basis": "CALENDAR_DEFAULT",
            "formal_engineering_fact": False,
        })

    margin_raw = mission.get("design_margin_ratio", 0)
    margin = _positive_number(margin_raw, "design_margin_ratio", allow_zero=True)
    if margin > 5:
        raise ValueError("design_margin_ratio:OUT_OF_RANGE")

    pe_per_day = _optional_number(workload.get("pe_cycles_per_day"))
    required_pe = None
    if pe_per_day is None:
        missing.append("PE_CYCLES_PER_DAY_REQUIRED_FOR_NAND_PE_BUDGET")
    elif pe_per_day < 0:
        raise ValueError("pe_cycles_per_day:NON_NEGATIVE_REQUIRED")
    elif years is not None:
        assessment = LifetimeEngine().assess(
            LifetimeAssessmentRequest(
                device_id="REQUIRED_STORAGE_PROFILE",
                assumptions=[
                    LifetimeAssumption(
                        name="pe_cycles_per_day",
                        value=pe_per_day,
                        unit="cycles_per_day",
                        rationale="EXPLICIT_WORKLOAD_PROFILE",
                    ),
                    LifetimeAssumption(
                        name="target_service_life_years",
                        value=years,
                        unit="years",
                        rationale="EXPLICIT_MISSION_PROFILE",
                    ),
                    LifetimeAssumption(
                        name="operating_days_per_year",
                        value=days_per_year,
                        unit="days_per_year",
                        rationale="MISSION_PROFILE_OR_EXPLICIT_CALENDAR_DEFAULT",
                    ),
                    LifetimeAssumption(
                        name="design_margin_ratio",
                        value=margin,
                        unit="ratio",
                        rationale="EXPLICIT_MISSION_PROFILE",
                    ),
                ],
            ),
            "NAND_REQUIRED_PE_BUDGET_V1",
        )
        if assessment.status.value == "CALCULATED":
            required_pe = float(assessment.result)
            trace.append({
                "requirement": "required_pe_cycles",
                "formula_id": assessment.formula_id,
                "formula_version": assessment.formula_version,
                "inputs": assessment.inputs,
                "result": required_pe,
                "unit": assessment.unit,
                "replay_trace": assessment.replay_trace,
            })
        else:
            missing.extend(assessment.missing_inputs or assessment.error_details or ["NAND_REQUIRED_PE_BUDGET_NOT_CALCULATED"])

    retention = _optional_number(mission.get("required_retention_years"))
    if retention is not None and retention < 0:
        raise ValueError("required_retention_years:NON_NEGATIVE_REQUIRED")

    ecc_bits = _optional_number(mission.get("minimum_ecc_correctable_bits"))
    ecc_step = _optional_number(mission.get("ecc_step_bytes"))
    if (ecc_bits is None) ^ (ecc_step is None):
        missing.append("ECC_REQUIREMENT_REQUIRES_BITS_AND_STEP_BYTES")

    max_temp = _optional_number(mission.get("max_operating_temperature_c"))

    return {
        "schema_version": "storage-required-profile/v0.1",
        "device_type": "NAND",
        "target_service_life_years": years,
        "required_pe_cycles": required_pe,
        "required_retention_years": retention,
        "minimum_ecc_correctable_bits": ecc_bits,
        "ecc_step_bytes": ecc_step,
        "max_operating_temperature_c": max_temp,
        "workload_stress": {
            "pe_cycles_per_day": pe_per_day,
            "logical_write_bytes_per_day": _optional_number(workload.get("logical_write_bytes_per_day")),
            "physical_write_bytes_per_day": _optional_number(workload.get("physical_write_bytes_per_day")),
            "write_amplification_factor": _optional_number(workload.get("write_amplification_factor")),
        },
        "derivation_trace": trace,
        "assumptions": assumptions,
        "missing_information": sorted(set(missing)),
        "status": "READY" if not missing else "PARTIAL",
        "decision_boundary": "NO_INFERRED_WAF_OR_WEAR_MODEL",
    }


def _facts_by_name(confirmed_facts: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("canonical_name") or "").strip(): item
        for item in (confirmed_facts or [])
        if str(item.get("canonical_name") or "").strip()
    }


def _number_from_fact(fact: dict[str, Any] | None) -> float | None:
    if not fact:
        return None
    value = fact.get("value")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(match.group(0)) if match else None


def _retention_years(fact: dict[str, Any] | None) -> float | None:
    if not fact:
        return None
    number = _number_from_fact(fact)
    unit = str(fact.get("unit") or "").lower()
    text = f"{fact.get('value') or ''} {unit}".lower()
    if number is None:
        return None
    if "year" in text or "年" in text or unit in {"y", "yr", "yrs"}:
        return number
    if "month" in text or "月" in text:
        return number / 12.0
    return None


def _ecc_capability(fact: dict[str, Any] | None) -> tuple[float, float] | None:
    if not fact:
        return None
    text = f"{fact.get('value') or ''} {fact.get('unit') or ''}"
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*bits?\s*(?:/|per)\s*(\d+(?:\.\d+)?)\s*(?:bytes?|b)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def _temperature_max_c(fact: dict[str, Any] | None) -> float | None:
    if not fact:
        return None
    text = f"{fact.get('value') or ''} {fact.get('unit') or ''}"
    numbers = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", text)]
    return max(numbers) if len(numbers) >= 2 else None


def match_candidate(
    required_profile: dict[str, Any],
    confirmed_facts: list[dict[str, Any]] | None,
    *,
    candidate_id: str = "",
) -> dict[str, Any]:
    """Conservative procurement-fit projection from confirmed Device Facts only."""

    facts = _facts_by_name(confirmed_facts)
    checks: list[dict[str, Any]] = []
    unknowns: list[str] = []

    required_pe = _optional_number(required_profile.get("required_pe_cycles"))
    if required_pe is not None:
        fact = facts.get("pe_cycles")
        actual = _number_from_fact(fact)
        if actual is None:
            unknowns.append("CANDIDATE_PE_CYCLES_UNKNOWN")
        else:
            checks.append({
                "requirement": "pe_cycles",
                "required": required_pe,
                "actual": actual,
                "unit": "cycles",
                "status": "PASS" if actual >= required_pe else "FAIL",
                "evidence": list((fact or {}).get("evidence") or []),
                "margin": actual - required_pe,
            })

    required_retention = _optional_number(required_profile.get("required_retention_years"))
    if required_retention is not None:
        fact = facts.get("data_retention") or facts.get("retention")
        actual = _retention_years(fact)
        if actual is None:
            unknowns.append("CANDIDATE_RETENTION_UNKNOWN_OR_UNIT_UNSUPPORTED")
        else:
            checks.append({
                "requirement": "data_retention",
                "required": required_retention,
                "actual": actual,
                "unit": "years",
                "status": "PASS" if actual >= required_retention else "FAIL",
                "evidence": list((fact or {}).get("evidence") or []),
                "margin": actual - required_retention,
            })

    required_ecc_bits = _optional_number(required_profile.get("minimum_ecc_correctable_bits"))
    required_ecc_step = _optional_number(required_profile.get("ecc_step_bytes"))
    if required_ecc_bits is not None and required_ecc_step is not None:
        fact = facts.get("ecc_capability")
        actual = _ecc_capability(fact)
        if actual is None:
            unknowns.append("CANDIDATE_ECC_CAPABILITY_UNKNOWN_OR_UNPARSEABLE")
        elif actual[1] != required_ecc_step:
            unknowns.append("CANDIDATE_ECC_STEP_NOT_COMPARABLE")
        else:
            checks.append({
                "requirement": "ecc_capability",
                "required": {"bits": required_ecc_bits, "step_bytes": required_ecc_step},
                "actual": {"bits": actual[0], "step_bytes": actual[1]},
                "status": "PASS" if actual[0] >= required_ecc_bits else "FAIL",
                "evidence": list((fact or {}).get("evidence") or []),
            })

    required_temp = _optional_number(required_profile.get("max_operating_temperature_c"))
    if required_temp is not None:
        fact = facts.get("operating_temperature") or facts.get("operating_temperature_range")
        actual = _temperature_max_c(fact)
        if actual is None:
            unknowns.append("CANDIDATE_OPERATING_TEMPERATURE_UNKNOWN")
        else:
            checks.append({
                "requirement": "max_operating_temperature_c",
                "required": required_temp,
                "actual": actual,
                "unit": "C",
                "status": "PASS" if actual >= required_temp else "FAIL",
                "evidence": list((fact or {}).get("evidence") or []),
                "margin": actual - required_temp,
            })

    if not checks and unknowns:
        status = UNKNOWN
    elif any(item["status"] == "FAIL" for item in checks):
        status = NOT_FIT
    elif unknowns:
        status = UNKNOWN
    elif checks:
        status = FIT
    else:
        status = UNKNOWN
        unknowns.append("NO_COMPARABLE_HARD_REQUIREMENT")

    return {
        "candidate_id": candidate_id,
        "status": status,
        "checks": checks,
        "unknowns": sorted(set(unknowns)),
        "formal_fact_only": True,
        "automatic_purchase_approval": False,
        "decision_boundary": "ENGINEERING_DECISION_SUPPORT_ONLY",
    }


def compose_role_views(
    *,
    device: dict[str, Any],
    required_profile: dict[str, Any],
    hardware_facts: list[dict[str, Any]],
    formal_nand_knowledge: dict[str, Any] | None,
    write_governance_result: dict[str, Any] | None = None,
    diagnostic_result: dict[str, Any] | None = None,
    lifetime_results: list[dict[str, Any]] | None = None,
    procurement_results: list[dict[str, Any]] | None = None,
    change_impact_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project existing engine/skill results into role-specific views."""

    knowledge = dict(formal_nand_knowledge or {})
    knowledge_items = list(knowledge.get("results") or knowledge.get("items") or [])
    item_evidence_ready = bool(
        knowledge_items
        and all(item.get("evidence_refs") for item in knowledge_items if isinstance(item, dict))
    )
    formal_ready = bool(item_evidence_ready or (knowledge_items and knowledge.get("evidence_refs")))
    blockers = list(required_profile.get("missing_information") or [])
    if not formal_ready:
        blockers.append("FORMAL_NAND_KNOWLEDGE_WITH_EVIDENCE_REQUIRED")

    software = dict(write_governance_result or {})
    diagnosis = dict(diagnostic_result or {})
    change = dict(change_impact_result or {})
    lifetimes = list(lifetime_results or [])
    procurement = list(procurement_results or [])

    if software and software.get("status") not in {"ANSWERED", "NOT_APPLICABLE"}:
        blockers.extend(software.get("missing_information") or [])
    if diagnosis and diagnosis.get("status") not in {"ANSWERED", "NOT_APPLICABLE"}:
        blockers.extend(diagnosis.get("missing_information") or [])
    if change and change.get("status") not in {"ANSWERED", "NOT_APPLICABLE"}:
        blockers.extend(change.get("missing_information") or [])

    lifetime_answered = [
        item for item in lifetimes
        if (item.get("skill_result") or item).get("status") in {"ANSWERED", "CALCULATED"}
    ]

    return {
        "schema_version": "storage-engineering-decision/v0.1",
        "device": device,
        "overall_status": "READY_FOR_DEMO" if formal_ready and not blockers else "PARTIAL_FAIL_CLOSED",
        "system_engineering": {
            "required_storage_profile": required_profile,
            "traceability": required_profile.get("derivation_trace") or [],
        },
        "hardware_engineering": {
            "confirmed_device_facts": hardware_facts,
            "fact_count": len(hardware_facts),
            "formal_knowledge_ready": formal_ready,
        },
        "software_engineering": software or {
            "status": "NOT_RUN",
            "missing_information": ["WRITE_GOVERNANCE_NOT_RUN"],
        },
        "procurement": {
            "candidate_results": procurement,
            "automatic_purchase_approval": False,
        },
        "test_validation": {
            "diagnostic": diagnosis or {"status": "NOT_RUN"},
            "validation_from_write_governance": (
                (software.get("structured_result") or {}).get("suggested_validation") or []
            ),
            "automatic_release_approval": False,
        },
        "change_risk": change or {
            "status": "NOT_RUN",
            "direct_answer": "未提供变更对象；不形成变更安全结论。",
        },
        "runtime_lifetime": {
            "lifetime_results": lifetimes,
            "answered_count": len(lifetime_answered),
            "diagnostic": diagnosis or {"status": "NOT_RUN"},
        },
        "formal_knowledge": {
            "ready": formal_ready,
            "knowledge_refs": sorted({
                str(item.get("object_id"))
                for item in knowledge_items
                if isinstance(item, dict) and item.get("object_id")
            }),
            "evidence_refs": sorted({
                str(ref)
                for item in knowledge_items
                if isinstance(item, dict)
                for ref in (item.get("evidence_refs") or [])
                if ref
            }),
        },
        "blockers": sorted(set(str(x) for x in blockers if str(x))),
        "rules": {
            "unknown_is_safe": False,
            "public_knowledge_is_formal_evidence": False,
            "second_runtime_stack": False,
            "second_knowledge_stack": False,
            "second_formula_stack": False,
        },
    }
