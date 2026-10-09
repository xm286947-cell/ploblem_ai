"""Thin, fail-closed seven-role view over existing Storage boundaries.

This adapter is deliberately TEST_ONLY until the required formal NAND release
exists. It does not create Candidates, write Knowledge, or call a Provider.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException

from . import product_api, templates
from .knowledge_release import KnowledgeReleaseConsumer, KnowledgeReleaseError
from .lifetime_engine import (
    LifetimeAssumption,
    LifetimeAssessmentRequest,
    LifetimeAssessmentStatus,
    LifetimeEngine,
)


_DOMAINS = (
    ("PE_ENDURANCE", "pe_cycles", "P/E Cycle", "NAND endurance P/E cycles ECC condition"),
    ("RETENTION", "data_retention", "Data Retention", "NAND retention years temperature condition"),
    ("ECC_BIT_FLIP", "ecc_capability", "ECC Capability", "NAND ECC corrected uncorrectable bit flip"),
    ("BAD_BLOCK", "runtime_bad_block", "Bad Block", "NAND factory runtime bad block BBT counter"),
)


def _evidence_ids(row: dict[str, Any]) -> list[str]:
    refs = list(row.get("evidence_refs") or [])
    refs.extend(
        str(item.get("evidence_id"))
        for item in (row.get("evidence") or [])
        if isinstance(item, dict) and item.get("evidence_id")
    )
    return sorted({str(item) for item in refs if item})


def _pe_endurance_claim(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Extract only a narrowly worded, evidence-bound P/E rating.

    This is a screening input, never a device qualification. Ambiguous values,
    missing evidence, and unstructured ECC applicability are deliberately ignored.
    """
    patterns = (
        re.compile(r"(?:p\s*/\s*e|program\s*/\s*erase|program[- ]erase)[^\d]{0,40}(100\s*,?\s*000|100\s*k)\s*(?:cycles?)?", re.I),
        re.compile(r"(100\s*,?\s*000|100\s*k)\s*(?:p\s*/\s*e|program\s*/\s*erase|program[- ]erase)[^\w]", re.I),
    )
    for row in rows:
        evidence = _evidence_ids(row)
        if not evidence:
            continue
        text = " ".join(str(row.get(key) or "") for key in ("title", "summary", "content"))
        conditions = row.get("conditions") or []
        if isinstance(conditions, str):
            conditions = [conditions]
        condition_text = " ".join(map(str, conditions))
        combined = f"{text} {condition_text}"
        match = None
        for pattern in patterns:
            match = pattern.search(combined)
            if match:
                break
        if not match:
            continue
        raw = re.sub(r"\s|,", "", match.group(1))
        rating = 100_000 if raw.lower() in {"100000", "100k"} else None
        if rating is None:
            continue
        requires_ecc = bool(re.search(r"\bwith\s+(?:internal\s+)?ecc\b|ecc\s+(?:enabled|on)", combined, re.I))
        return {
            "value": rating,
            "unit": "cycles",
            "condition": "WITH_ECC" if requires_ecc else "AS_STATED_IN_RELEASE",
            "knowledge_id": row.get("object_id"),
            "evidence_refs": evidence,
            "source_refs": sorted({str(item) for item in (row.get("source_refs") or []) if item}),
            "requires_ecc": requires_ecc,
        }
    return None


def _endurance_screening(profile: dict[str, Any], claim: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
    required = profile.get("required_pe_cycles")
    if claim is None or required is None:
        return {"status": "UNKNOWN", "reason": "EVIDENCE_BOUND_PE_RATING_OR_REQUIRED_BUDGET_MISSING"}
    if claim["requires_ecc"]:
        ecc_enabled = (payload.get("system_conditions") or {}).get("internal_ecc_enabled")
        if ecc_enabled is not True:
            return {
                "status": "UNKNOWN",
                "reason": "SOURCE_RATING_REQUIRES_ECC_APPLICABILITY_INPUT",
                "required_condition": "internal_ecc_enabled=true",
                "knowledge_id": claim["knowledge_id"],
                "evidence_refs": claim["evidence_refs"],
            }
    try:
        required_value = float(required)
    except (TypeError, ValueError):
        return {"status": "UNKNOWN", "reason": "REQUIRED_PE_BUDGET_NOT_NUMERIC"}
    within = required_value <= claim["value"]
    return {
        "status": "WITHIN_RATING_SCREEN" if within else "EXCEEDS_RATING_SCREEN",
        "required_pe_cycles": required_value,
        "source_rated_pe_cycles": claim["value"],
        "screening_margin_ratio": round((claim["value"] - required_value) / claim["value"], 6),
        "condition": claim["condition"],
        "knowledge_id": claim["knowledge_id"],
        "evidence_refs": claim["evidence_refs"],
        "source_refs": claim["source_refs"],
        "qualification": "NOT_ESTABLISHED_SCREENING_ONLY",
    }


def _profile(device_id: str, mission: dict[str, Any], workload: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    assumptions: list[dict[str, Any]] = []
    trace: dict[str, Any] = {}
    raw = {
        "pe_cycles_per_day": (workload.get("pe_cycles_per_day"), "cycles_per_day"),
        "target_service_life_years": (mission.get("target_service_life_years"), "years"),
        "operating_days_per_year": (mission.get("operating_days_per_year", 365), "days_per_year"),
        "design_margin_ratio": (mission.get("design_margin_ratio", 0), "ratio"),
    }
    for key, (value, _) in raw.items():
        if value in (None, ""):
            missing.append(
                "PE_CYCLES_PER_DAY_REQUIRED_FOR_NAND_PE_BUDGET"
                if key == "pe_cycles_per_day" else key.upper() + "_REQUIRED"
            )
    result = None
    if not missing:
        assumptions = []
        for key, (value, unit) in raw.items():
            if key == "operating_days_per_year" and key not in mission:
                rationale = "CALENDAR_DEFAULT_365_DAYS_PER_YEAR_NOT_DEVICE_FACT"
            elif key == "design_margin_ratio" and key not in mission:
                rationale = "DEFAULT_ZERO_MARGIN_NOT_DEVICE_FACT"
            else:
                rationale = "EXPLICIT_CONTROLLED_TEST_INPUT"
            assumptions.append(LifetimeAssumption(name=key, value=value, unit=unit, rationale=rationale))
        assessed = LifetimeEngine().assess(
            LifetimeAssessmentRequest(device_id=device_id, assumptions=assumptions),
            "NAND_REQUIRED_PE_BUDGET_V1",
        )
        if assessed.status is LifetimeAssessmentStatus.CALCULATED:
            result = assessed.result
            trace = assessed.model_dump(mode="json")
        else:
            missing.extend(assessed.missing_inputs or assessed.error_details)
    return {
        "schema_version": "storage-required-profile/v0.1",
        "device_type": "NAND Flash",
        "target_service_life_years": mission.get("target_service_life_years"),
        "required_pe_cycles": result,
        "required_retention_years": mission.get("required_retention_years"),
        "minimum_ecc_correctable_bits": mission.get("minimum_ecc_correctable_bits"),
        "ecc_step_bytes": mission.get("ecc_step_bytes"),
        "max_operating_temperature_c": mission.get("max_operating_temperature_c"),
        "workload_stress": {key: value for key, (value, _) in raw.items() if key in workload or key in mission},
        "derivation_trace": trace,
        "assumptions": [item.model_dump(mode="json") for item in assumptions],
        "missing_information": sorted(set(missing)),
        "status": "READY" if result is not None and not missing else "PARTIAL",
        "decision_boundary": "REQUIREMENT_SCREENING_ONLY_NOT_DEVICE_QUALIFICATION",
    }


def build_nand_engineering_decision(device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    detail = product_api.device_slots(device_id)
    device = detail["device"]
    device_type = templates.normalize_device_type(str(device.get("device_type") or ""))
    if device_type != "NAND Flash":
        raise ValueError(f"NAND_ENGINEERING_DECISION_REQUIRES_NAND:{device_type}")

    mission = dict(payload.get("mission_profile") or {})
    workload = dict(payload.get("workload_profile") or {})
    profile = _profile(device_id, mission, workload)
    facts = list(detail.get("device_facts") or [])
    fact_evidence = sorted({
        str(ev.get("evidence_id"))
        for fact in facts for ev in (fact.get("evidence") or [])
        if isinstance(ev, dict) and ev.get("evidence_id")
    })

    knowledge_domains: list[dict[str, Any]] = []
    knowledge_objects: list[dict[str, Any]] = []
    domain_objects: dict[str, list[dict[str, Any]]] = {}
    consumer = KnowledgeReleaseConsumer.current()
    release_status = consumer.status() if hasattr(consumer, "status") else {}
    try:
        binding = consumer.validate_storage_binding()
        bound_version = str(binding.get("knowledge_release_version") or "")
    except KnowledgeReleaseError as error:
        binding = None
        bound_version = ""
        binding_error = str(error) or "STORAGE_RELEASE_BINDING_INVALID"
    except (OSError, ValueError, TypeError):
        binding = None
        bound_version = ""
        binding_error = "STORAGE_RELEASE_BINDING_INVALID"
    else:
        binding_error = ""
    for domain, canonical, label, context in _DOMAINS:
        result = (
            product_api._formal_knowledge(canonical, label, device_type, context=context, top_k=5)
            if binding else {
                "status": "UNKNOWN", "code": binding_error,
                "knowledge_release_version": None, "results": [], "evidence_refs": [],
            }
        )
        rows = list(result.get("results") or [])
        if rows and str(result.get("knowledge_release_version") or "") != bound_version:
            rows = []
        domain_objects[domain] = rows
        refs = sorted({str(ref) for row in rows for ref in (row.get("evidence_refs") or []) if ref})
        domain_code = result.get("code")
        if result.get("results") and not rows:
            domain_code = "RELEASE_VERSION_BINDING_MISMATCH"
        elif rows and not refs:
            domain_code = "FORMAL_EVIDENCE_REQUIRED"
        knowledge_domains.append({
            "domain": domain,
            "status": "MATCHED" if rows and refs else "UNKNOWN",
            "code": domain_code,
            "knowledge_release_version": result.get("knowledge_release_version"),
            "result_count": len(rows),
            "evidence_refs": refs,
        })
        knowledge_objects.extend(rows)
    formal_evidence = sorted({ref for item in knowledge_domains for ref in item["evidence_refs"]})
    formal_ready = all(item["status"] == "MATCHED" for item in knowledge_domains)
    endurance_claim = _pe_endurance_claim(domain_objects.get("PE_ENDURANCE", []))
    endurance_screen = _endurance_screening(profile, endurance_claim, payload)
    runtime_telemetry = payload.get("runtime_telemetry")
    telemetry_items = (
        [runtime_telemetry] if isinstance(runtime_telemetry, dict)
        else [item for item in runtime_telemetry or [] if isinstance(item, dict)]
    )
    runtime_view = {
        "status": "UNKNOWN",
        "telemetry_status": "TEST_INPUT_PROVIDED_NOT_DEVICE_OBSERVATION" if telemetry_items else "NOT_PROVIDED",
        "telemetry": telemetry_items,
        "required_telemetry": ["per-block erase count / wear distribution", "ECC corrected and uncorrectable", "runtime bad-block changes"],
        "inference": "TELEMETRY_THRESHOLDS_AND_DEVICE_BASELINE_REQUIRED_BEFORE_DEGRADATION_CONCLUSION",
    }
    shared_case = {
        "case_id": str(payload.get("case_id") or f"TEST_ONLY:{device_id}"),
        "classification": "TEST_ONLY",
        "business_requirement": {
            "target_service_life_years": mission.get("target_service_life_years"),
            "required_retention_years": mission.get("required_retention_years"),
        },
        "device": {key: device.get(key) for key in ("id", "device_type", "vendor", "model") if key in device},
        "source_facts": facts,
        "source_fact_evidence_refs": fact_evidence,
        "formal_knowledge": {
            "status": "READY" if formal_ready else "UNKNOWN",
            "release_identity": {
                "version": bound_version or release_status.get("knowledge_release_version"),
                "snapshot_hash": release_status.get("snapshot_hash"),
                "classification": "CONTROLLED_VALIDATION_RELEASE_NOT_CUSTOMER_QUALIFICATION",
            },
            "domains": knowledge_domains,
            "knowledge_ids": sorted({str(row.get("object_id")) for row in knowledge_objects if row.get("object_id")}),
            "evidence_refs": formal_evidence,
        },
        "controlled_inputs": {"mission_profile": mission, "workload_profile": workload},
        "required_profile": profile,
        "engineering_screens": {"pe_endurance": endurance_screen},
        "device_decision": "INSUFFICIENT_EVIDENCE",
    }

    blockers = list(profile["missing_information"])
    if not formal_ready:
        blockers.extend(f"FORMAL_KNOWLEDGE_DOMAIN_UNKNOWN:{item['domain']}" for item in knowledge_domains if item["status"] != "MATCHED")
    roles = {
        "system_engineering": {
            "status": "ACTIONABLE",
            "required_profile": profile,
            "open_knowledge_domains": [item["domain"] for item in knowledge_domains if item["status"] != "MATCHED"],
            "pe_endurance_screen": endurance_screen,
            "next_actions": ["确认真实 P/E stress 与工作负载，再进行规格冻结。", "保留需求预算与器件资格结论的边界。"],
        },
        "hardware_engineering": {
            "status": "ACTIONABLE" if facts or formal_ready else "UNKNOWN",
            "confirmed_facts": facts,
            "formal_knowledge_domains": knowledge_domains,
            "pe_endurance_screen": endurance_screen,
            "evidence_refs": endurance_screen.get("evidence_refs", []),
            "next_actions": ["补齐有来源和适用条件的 Endurance、Retention、ECC 与 Bad Block 正式知识。"],
        },
        "software_engineering": {
            "status": "ACTIONABLE",
            "controlled_workload_inputs": workload,
            "required_pe_cycles": profile.get("required_pe_cycles"),
            "pe_endurance_screen": endurance_screen,
            "next_actions": ["测量并约束主机写入量、介质写入量和 WAF；不得从逻辑写入推测 P/E stress。", "记录日志、WAL、Flush、GC 与磨损均衡相关负载。"],
        },
        "procurement": {
            "status": "ACTIONABLE" if endurance_screen.get("status") != "UNKNOWN" else "UNKNOWN",
            "decision": "UNKNOWN",
            "endurance_screen": endurance_screen,
            "unknowns": ["FORMAL_ENDURANCE_CONDITION", "ORDERABLE_PART_SCOPE", "RETENTION_CONDITION", "ECC_AND_BAD_BLOCK_BOUNDARY"],
            "decision_boundary": "SCREENING_DOES_NOT_QUALIFY_OR_APPROVE_DEVICE",
            "automatic_purchase_approval": False,
        },
        "test_validation": {
            "status": "ACTIONABLE",
            "is_test_result": False,
            "proposed_checks": ["记录 host/media writes 与实际 WAF", "覆盖工作负载和温度条件", "采集 P/E 分布、ECC corrected/uncorrectable 与坏块遥测", "按批准阈值判定并回填同一 Case"],
            "knowledge_basis": {"pe_endurance_screen": endurance_screen, "formal_evidence_refs": formal_evidence},
        },
        "change_management": {
            "status": "ACTIONABLE",
            "reassessment_triggers": ["NAND 料号/类型/容量变化", "ECC/控制器/固件变化", "OP、文件系统、数据库或写策略变化", "工作负载或实测 WAF/P/E 分布变化"],
            "reassessment_baseline": {
                "knowledge_release_version": shared_case["formal_knowledge"]["release_identity"]["version"],
                "knowledge_snapshot_hash": shared_case["formal_knowledge"]["release_identity"]["snapshot_hash"],
                "knowledge_ids": shared_case["formal_knowledge"]["knowledge_ids"],
                "evidence_refs": formal_evidence,
            },
            "safe_by_default": False,
        },
        "runtime_lifetime": {
            **runtime_view,
        },
    }
    return {
        "schema_version": "storage-engineering-decision/v0.2",
        "classification": "TEST_ONLY",
        "overall_status": "PARTIAL_FAIL_CLOSED" if blockers else "TEST_ONLY_REVIEW_REQUIRED",
        "shared_case": shared_case,
        "roles": roles,
        "blockers": sorted(set(blockers)),
        "rules": {
            "shared_case_single_source": True,
            "public_knowledge_is_formal_evidence": False,
            "unknown_is_safe": False,
            "formal_publish_performed": False,
            "provider_call_performed": False,
            "test_output_is_human_approval": False,
        },
    }


def create_nand_engineering_decision_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/product/devices/{device_id}/engineering-decision/nand")
    def nand_engineering_decision(device_id: str, payload: dict[str, Any]):
        try:
            return build_nand_engineering_decision(device_id, payload)
        except KeyError as error:
            raise HTTPException(404, "DEVICE_NOT_FOUND") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
