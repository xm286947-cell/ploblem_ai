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


def _retention_claim(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    pattern = re.compile(r"(?:data\s+retention|retention)[^\d]{0,30}(\d+(?:\.\d+)?)\s*(years?|yrs?)\b", re.I)
    for row in rows:
        evidence = _evidence_ids(row)
        if not evidence:
            continue
        text = " ".join(str(row.get(key) or "") for key in ("title", "summary", "content"))
        match = pattern.search(text)
        if match:
            return {
                "value": float(match.group(1)), "unit": "years",
                "knowledge_id": row.get("object_id"), "evidence_refs": evidence,
                "source_refs": sorted({str(item) for item in (row.get("source_refs") or []) if item}),
                "conditions": row.get("conditions") or [],
            }
    return None


def _evidence_screen(required: Any, claim: dict[str, Any] | None, *, unit: str) -> dict[str, Any]:
    if claim is None:
        return {"status": "UNKNOWN", "reason": f"EVIDENCE_BOUND_{unit.upper()}_RATING_MISSING"}
    if required in (None, ""):
        return {
            "status": "UNKNOWN", "reason": f"REQUIRED_{unit.upper()}_INPUT_MISSING",
            "knowledge_id": claim["knowledge_id"], "evidence_refs": claim["evidence_refs"],
        }
    try:
        required_value = float(required)
    except (TypeError, ValueError):
        return {"status": "UNKNOWN", "reason": f"REQUIRED_{unit.upper()}_INPUT_INVALID"}
    return {
        "status": "WITHIN_RATING_SCREEN" if required_value <= claim["value"] else "EXCEEDS_RATING_SCREEN",
        "required_value": required_value, "rated_value": claim["value"], "unit": claim["unit"],
        "knowledge_id": claim["knowledge_id"], "evidence_refs": claim["evidence_refs"],
        "source_refs": claim["source_refs"], "conditions": claim.get("conditions", []),
        "qualification": "NOT_ESTABLISHED_SCREENING_ONLY",
    }


def _ecc_screen(rows: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    evidenced = [row for row in rows if _evidence_ids(row)]
    if not evidenced:
        return {"status": "UNKNOWN", "reason": "EVIDENCE_BOUND_ECC_KNOWLEDGE_MISSING"}
    refs = sorted({ref for row in evidenced for ref in _evidence_ids(row)})
    ids = sorted({str(row.get("object_id")) for row in evidenced if row.get("object_id")})
    mode = (payload.get("system_conditions") or {}).get("internal_ecc_enabled")
    if mode is False:
        return {"status": "CONDITION_MISMATCH", "reason": "INTERNAL_ECC_DISABLED", "knowledge_ids": ids, "evidence_refs": refs}
    if mode is not True:
        return {"status": "UNKNOWN", "reason": "INTERNAL_ECC_APPLICABILITY_NOT_PROVIDED", "knowledge_ids": ids, "evidence_refs": refs}
    return {
        "status": "CONDITION_APPLICABLE_LIMIT_NOT_ESTABLISHED",
        "reason": "ECC_MODE_CONDITION_IS_EVIDENCED_BUT_CORRECTION_STRENGTH_IS_NOT_ASSERTED",
        "knowledge_ids": ids, "evidence_refs": refs,
    }


def _bad_block_screen(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_rows = [row for row in rows if _evidence_ids(row)]
    if not evidence_rows:
        return {"status": "UNKNOWN", "reason": "EVIDENCE_BOUND_BAD_BLOCK_KNOWLEDGE_MISSING"}
    text = " ".join(
        str(row.get(key) or "") for row in evidence_rows for key in ("title", "summary", "content")
    )
    refs = sorted({ref for row in evidence_rows for ref in _evidence_ids(row)})
    if re.search(r"(?:not\s+(?:declared|specified)|does\s+not\s+(?:declare|specify)|未声明|未定义|没有.*(?:计数器|counter))", text, re.I):
        return {"status": "RUNTIME_COUNTER_NOT_DECLARED", "reason": "NO_RUNTIME_CUMULATIVE_BAD_BLOCK_COUNTER_DECLARED", "evidence_refs": refs}
    return {"status": "BOUNDARY_KNOWN_RUNTIME_TELEMETRY_UNKNOWN", "reason": "STATIC_BAD_BLOCK_DATA_IS_NOT_RUNTIME_TELEMETRY", "evidence_refs": refs}


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

    part_number_model_id = str(payload.get("part_number_model_id") or "").strip()
    available_part_models = [
        item for item in (detail.get("orderable_part_candidates") or [])
        if item.get("verify_status") != "rejected"
    ]
    if available_part_models and not part_number_model_id:
        raise ValueError("PART_NUMBER_SELECTION_REQUIRED")
    part_selection = None
    if part_number_model_id:
        part_selection = product_api.part_number_facts(device_id, part_number_model_id)
        if not part_selection.get("can_consume"):
            raise ValueError("PART_NUMBER_REVIEW_REQUIRED")

    mission = dict(payload.get("mission_profile") or {})
    workload = dict(payload.get("workload_profile") or {})
    profile = _profile(device_id, mission, workload)
    facts = list(
        part_selection.get("confirmed_facts") if part_selection
        else detail.get("device_facts") or []
    )
    part_number = (part_selection or {}).get("selected_part", {}).get("part_number")
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
        returned_rows = list(result.get("results") or [])
        release_matches = (
            not returned_rows
            or str(result.get("knowledge_release_version") or "") == bound_version
        )
        if not release_matches:
            returned_rows = []
        # Releases that declare a domain are scoped here, preventing a broad
        # keyword hit from making one fact appear to satisfy unrelated domains.
        scoped_rows = [
            row for row in returned_rows
            if not row.get("storage_domain") or str(row.get("storage_domain")).upper() == domain
        ]
        if part_number:
            # A family/device-domain Knowledge hit is not proof that a rating
            # applies to the selected orderable part. Require an explicit exact
            # part reference before using it for a part-scoped engineering screen.
            needle = re.sub(r"[^a-z0-9]", "", part_number.casefold())
            part_rows = []
            for row in scoped_rows:
                declared = [
                    row.get(key) for key in
                    ("part_number", "part_numbers", "scope", "scope_values", "applicability")
                ]
                declared_text = " ".join(
                    str(value) for value in declared if value is not None
                )
                text = " ".join(
                    str(row.get(key) or "") for key in ("title", "summary", "content", "conditions")
                )
                haystack = re.sub(r"[^a-z0-9]", "", f"{declared_text} {text}".casefold())
                if needle and needle in haystack:
                    part_rows.append(row)
            scoped_rows = part_rows
        # A release result without row-level evidence is not a consumable fact.
        rows = [row for row in scoped_rows if _evidence_ids(row)]
        domain_objects[domain] = rows
        refs = sorted({ref for row in rows for ref in _evidence_ids(row)})
        knowledge_ids = sorted({str(row.get("object_id")) for row in rows if row.get("object_id")})
        source_refs = sorted({str(ref) for row in rows for ref in (row.get("source_refs") or []) if ref})
        conditions = sorted({
            str(condition)
            for row in rows
            for condition in (
                row.get("conditions") if isinstance(row.get("conditions"), list)
                else [row.get("conditions")] if row.get("conditions") else []
            )
        })
        domain_code = result.get("code")
        if part_number and returned_rows and not scoped_rows:
            domain_code = "PART_NUMBER_APPLICABILITY_NOT_ESTABLISHED"
        elif returned_rows and scoped_rows and not rows:
            domain_code = "FORMAL_EVIDENCE_REQUIRED"
        elif returned_rows and not scoped_rows:
            domain_code = "KNOWLEDGE_DOMAIN_SCOPE_MISMATCH"
        elif result.get("results") and not release_matches:
            domain_code = "RELEASE_VERSION_BINDING_MISMATCH"
        knowledge_domains.append({
            "domain": domain,
            "status": "MATCHED" if rows and refs else "UNKNOWN",
            "code": domain_code,
            "knowledge_release_version": result.get("knowledge_release_version"),
            "result_count": len(rows),
            "knowledge_ids": knowledge_ids,
            "evidence_refs": refs,
            "source_refs": source_refs,
            "conditions": conditions,
        })
        knowledge_objects.extend(rows)
    formal_evidence = sorted({ref for item in knowledge_domains for ref in item["evidence_refs"]})
    formal_ready = all(item["status"] == "MATCHED" for item in knowledge_domains)
    endurance_claim = _pe_endurance_claim(domain_objects.get("PE_ENDURANCE", []))
    endurance_screen = _endurance_screening(profile, endurance_claim, payload)
    retention_claim = _retention_claim(domain_objects.get("RETENTION", []))
    retention_screen = _evidence_screen(
        mission.get("required_retention_years"), retention_claim, unit="retention_years"
    )
    ecc_screen = _ecc_screen(domain_objects.get("ECC_BIT_FLIP", []), payload)
    bad_block_screen = _bad_block_screen(domain_objects.get("BAD_BLOCK", []))
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
        "device": {
            **{key: device.get(key) for key in ("id", "device_type", "vendor", "model") if key in device},
            **({"orderable_part_number": part_number,
                "part_number_model_id": part_number_model_id,
                "part_number_review_status": (part_selection or {}).get("selected_part", {}).get("verify_status")}
               if part_selection else {}),
        },
        "source_facts": facts,
        "source_fact_evidence_refs": fact_evidence,
        "source_fact_evidence": [
            {
                "candidate_id": fact.get("candidate_id"),
                "canonical_name": fact.get("canonical_name"),
                "scope": fact.get("scope"),
                "evidence": fact.get("evidence") or [],
            }
            for fact in facts
        ],
        "formal_knowledge": {
            "status": "READY" if formal_ready else "UNKNOWN",
            "release_identity": {
                "version": bound_version or release_status.get("knowledge_release_version"),
                "snapshot_hash": release_status.get("snapshot_hash"),
                "binding_snapshot_hash": (binding or {}).get("knowledge_release_snapshot_hash"),
                "classification": (binding or {}).get("release_class") or "CONTROLLED_VALIDATION_RELEASE",
                "qualification_state": (binding or {}).get("qualification_state") or "NOT_CUSTOMER_QUALIFICATION",
            },
            "domains": knowledge_domains,
            "knowledge_ids": sorted({str(row.get("object_id")) for row in knowledge_objects if row.get("object_id")}),
            "evidence_refs": formal_evidence,
        },
        "controlled_inputs": {"mission_profile": mission, "workload_profile": workload},
        "required_profile": profile,
        "engineering_screens": {
            "pe_endurance": endurance_screen,
            "retention": retention_screen,
            "ecc_bit_flip": ecc_screen,
            "bad_block": bad_block_screen,
        },
        "device_decision": "INSUFFICIENT_EVIDENCE",
    }

    blockers = list(profile["missing_information"])
    if not formal_ready:
        blockers.extend(f"FORMAL_KNOWLEDGE_DOMAIN_UNKNOWN:{item['domain']}" for item in knowledge_domains if item["status"] != "MATCHED")
    matched_domains = {item["domain"] for item in knowledge_domains if item["status"] == "MATCHED"}
    knowledge_basis = {
        item["domain"]: {
            "status": item["status"],
            "knowledge_ids": item["knowledge_ids"],
            "evidence_refs": item["evidence_refs"],
            "source_refs": item["source_refs"],
            "conditions": item["conditions"],
            "release_version": item["knowledge_release_version"],
        }
        for item in knowledge_domains
    }
    pe_status = endurance_screen.get("status")
    if pe_status == "WITHIN_RATING_SCREEN":
        procurement_screen = "WITHIN_P_E_RATING_SCREEN_ONLY"
    elif pe_status == "EXCEEDS_RATING_SCREEN":
        procurement_screen = "EXCEEDS_P_E_RATING_SCREEN"
    else:
        procurement_screen = "UNKNOWN"
    procurement_unknowns = [
        domain for domain in ("RETENTION", "ECC_BIT_FLIP", "BAD_BLOCK")
        if domain not in matched_domains
    ]
    if not profile.get("required_retention_years"):
        procurement_unknowns.append("REQUIRED_RETENTION_CONDITION")
    if profile.get("minimum_ecc_correctable_bits") is None:
        procurement_unknowns.append("SYSTEM_ECC_REQUIREMENT")
    procurement_next_actions = []
    if procurement_screen == "EXCEEDS_P_E_RATING_SCREEN":
        procurement_next_actions.append("在任何选型放行前，先核实该 P/E 预算、ECC 适用条件及工作负载；当前仅表示筛查超出额定值。")
    elif procurement_screen == "WITHIN_P_E_RATING_SCREEN_ONLY":
        procurement_next_actions.append("P/E 子项位于资料额定值筛查范围内；不得据此批准器件，继续关闭 Retention、ECC/Bit Flip、Bad Block 和料号适用范围。")
    else:
        procurement_next_actions.append("向供应商索取有来源、条件和准确订货料号范围的 P/E/Endurance 资料后再做比较。")
    if procurement_unknowns:
        procurement_next_actions.append("补齐未关闭知识/需求项：" + ", ".join(sorted(set(procurement_unknowns))) + "。")
    roles = {
        "system_engineering": {
            "status": "ACTIONABLE",
            "required_profile": profile,
            "open_knowledge_domains": [item["domain"] for item in knowledge_domains if item["status"] != "MATCHED"],
            "knowledge_basis": knowledge_basis,
            "pe_endurance_screen": endurance_screen,
            "retention_screen": retention_screen,
            "ecc_screen": ecc_screen,
            "bad_block_screen": bad_block_screen,
            "next_actions": ["确认真实 P/E stress 与工作负载，再进行规格冻结。", "保留需求预算与器件资格结论的边界。"],
        },
        "hardware_engineering": {
            "status": "ACTIONABLE" if facts or formal_ready else "UNKNOWN",
            "confirmed_facts": facts,
            "formal_knowledge_domains": knowledge_domains,
            "knowledge_basis": knowledge_basis,
            "pe_endurance_screen": endurance_screen,
            "retention_screen": retention_screen,
            "ecc_screen": ecc_screen,
            "bad_block_screen": bad_block_screen,
            "evidence_refs": endurance_screen.get("evidence_refs", []),
            "next_actions": [
                "补齐有来源和适用条件的 Endurance、Retention、ECC 与 Bad Block 正式知识。"
                if not formal_ready else "核对各知识对象的料号范围、工作条件和证据定位，再决定规格约束。"
            ],
        },
        "software_engineering": {
            "status": "ACTIONABLE",
            "controlled_workload_inputs": workload,
            "required_pe_cycles": profile.get("required_pe_cycles"),
            "pe_endurance_screen": endurance_screen,
            "knowledge_basis": {key: knowledge_basis[key] for key in ("PE_ENDURANCE", "ECC_BIT_FLIP")},
            "retention_screen": retention_screen,
            "ecc_screen": ecc_screen,
            "bad_block_screen": bad_block_screen,
            "design_constraints": [
                "实测主机写入量、介质写入量和 WAF；不可由逻辑写入量直接推断 P/E stress。",
                "将 ECC 配置和可纠正/不可纠正错误计数纳入软件/固件接口及日志。"
                if "ECC_BIT_FLIP" in matched_domains else "明确 ECC 配置，并建立可纠正/不可纠正错误计数采集接口；阈值需由正式资料或批准要求提供。",
                "将断电保持时长、存储温度和 P/E 状态纳入需求验证；本 Release 额定年限只用于条件筛查。"
                if "RETENTION" in matched_domains else "Retention 证据未关联；不得将标称年限当作目标环境下的保证。",
                "坏块累计计数器未由数据手册声明；需确认控制器/固件遥测来源，不能把静态坏块上限当运行态计数。"
                if bad_block_screen.get("status") == "RUNTIME_COUNTER_NOT_DECLARED" else "建立运行态坏块观测接口，并将规格边界与运行遥测分开。",
            ],
            "next_actions": ["测量并约束主机写入量、介质写入量和 WAF；不得从逻辑写入推测 P/E stress。", "记录日志、WAL、Flush、GC 与磨损均衡相关负载。"],
        },
        "procurement": {
            "status": "ACTIONABLE" if procurement_screen != "UNKNOWN" or matched_domains else "UNKNOWN",
            "decision": "UNKNOWN",
            "screening_result": procurement_screen,
            "endurance_screen": endurance_screen,
            "retention_screen": retention_screen,
            "ecc_screen": ecc_screen,
            "bad_block_screen": bad_block_screen,
            "knowledge_basis": knowledge_basis,
            "unknowns": sorted(set(procurement_unknowns + ["ORDERABLE_PART_SCOPE"])),
            "next_actions": procurement_next_actions,
            "decision_boundary": "SCREENING_DOES_NOT_QUALIFY_OR_APPROVE_DEVICE",
            "automatic_purchase_approval": False,
        },
        "test_validation": {
            "status": "ACTIONABLE",
            "is_test_result": False,
            "proposed_checks": ["记录 host/media writes 与实际 WAF", "覆盖工作负载和温度条件", "采集 P/E 分布、ECC corrected/uncorrectable 与坏块遥测", "按批准阈值判定并回填同一 Case"],
            "knowledge_basis": knowledge_basis,
            "test_basis_by_domain": {
                "PE_ENDURANCE": "按正式资料的 P/E 额定值及 ECC 条件设计筛查，不将筛查当作寿命资格。" if "PE_ENDURANCE" in matched_domains else "P/E 判据来源缺失；先补正式资料，测试阈值保持 UNKNOWN。",
                "RETENTION": "按正式资料声明的温度、数据保持时间和寿命条件制定验证。" if "RETENTION" in matched_domains else "Retention 条件/判据缺失；测试阈值保持 UNKNOWN。",
                "ECC_BIT_FLIP": "按正式 ECC 能力及错误语义制定采集和拦截项。" if "ECC_BIT_FLIP" in matched_domains else "ECC/Bit Flip 判据缺失；测试阈值保持 UNKNOWN。",
                "BAD_BLOCK": (
                    "受控证据记录：数据手册未声明运行累计坏块计数器；不得构造运行阈值，需确认控制器/固件遥测能力并验证可观测性。"
                    if bad_block_screen.get("status") == "RUNTIME_COUNTER_NOT_DECLARED" else
                    "按正式证据中明确的坏块管理边界设计验证；静态最大坏块数不得当作运行计数阈值。"
                    if "BAD_BLOCK" in matched_domains else
                    "运行态坏块计数/处理规则缺失；不得从静态最大坏块数构造运行阈值。"
                ),
            },
        },
        "change_management": {
            "status": "ACTIONABLE",
            "reassessment_triggers": ["NAND 料号/类型/容量变化", "ECC/控制器/固件变化", "OP、文件系统、数据库或写策略变化", "工作负载或实测 WAF/P/E 分布变化"],
            "reassessment_baseline": {
                "knowledge_release_version": shared_case["formal_knowledge"]["release_identity"]["version"],
                "knowledge_snapshot_hash": shared_case["formal_knowledge"]["release_identity"]["snapshot_hash"],
                "knowledge_ids": shared_case["formal_knowledge"]["knowledge_ids"],
                "evidence_refs": formal_evidence,
                "domain_basis": knowledge_basis,
            },
            "safe_by_default": False,
        },
        "runtime_lifetime": {
            **runtime_view,
            "knowledge_basis": {key: knowledge_basis[key] for key in ("ECC_BIT_FLIP", "BAD_BLOCK")},
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
            "selected_part_required_for_part_scoped_consumption": True,
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
