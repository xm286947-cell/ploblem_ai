"""Evidence-preserving five-dimensional *preview* of a single quality scenario.

Stacked on the opt-in, read-only QS Next gateway. No AI, writes, lifecycle
classification, taxonomy invention, scenario publish, or legacy modification.
Field aliases are copied from the 2026-09-13 mature source semantics.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from quality_knowledge.qs_next_gateway import (
    EntryGateError, _source, read_only_db, resolve_entry,
)

CONTRACT_VERSION = "quality-scenario-five-dimension-preview/v1"

# Reuse the legacy names/semantic categories instead of a new opaque text blob.
# Every mapped value originates from an explicitly named original material field.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "product_code": ("问题信息_产品编码", "产品编码"),
    "product_model": ("问题信息_产品型号", "产品型号"),
    "product_line": ("问题信息_产品线", "产品线"),
    "customer_name": ("问题信息_客户名称", "客户名称"),
    "customer_industry": ("问题信息_客户行业", "客户行业"),
    "user_type": ("user_type", "用户类型"),
    "lifecycle_code": ("lifecycle_code", "生命周期代码"),
    "activity_code": ("activity_code", "业务活动代码"),
    "software_usage_scenario": ("software_usage_scenario", "软件使用场景"),
    "customer_perception": ("customer_perception", "客户感知"),
    "symptom": ("问题信息_故障现象描述", "故障现象描述", "问题信息_问题描述", "问题描述"),
    "failure_mode": ("failure_mode", "失效模式"),
    "failure_mechanism": ("failure_mechanism", "失效机理"),
    "software_failure_mode": ("技术根因分析与纠正_产品层级软件失效模式", "产品层级软件失效模式"),
    "software_failure_mechanism": ("技术根因分析与纠正_功能层级软件失效机理", "功能层级软件失效机理"),
    "hardware_failure_mode": ("技术根因分析与纠正_器件失效模式", "器件失效模式"),
    "hardware_failure_mechanism": ("技术根因分析与纠正_器件失效机理", "器件失效机理"),
    "mechanical_failure_mode": ("技术根因分析与纠正_产品层级机械失效模式", "产品层级机械失效模式"),
    "mechanical_failure_mechanism": ("技术根因分析与纠正_部件层级机械机理/根因", "部件层级机械机理/根因"),
    "root_cause": ("技术根因分析与纠正_TRC根因", "技术根因分析与纠正_根因分析", "问题信息_问题原因定位"),
    "preconditions": ("preconditions", "前置条件"),
    "trigger_conditions": ("trigger_conditions", "触发条件"),
    "operating_environment": ("operating_environment", "运行环境"),
    "operating_condition": ("operating_condition", "运行工况"),
    "duration_frequency": ("duration_frequency", "持续时长或频率"),
    "disturbances": ("disturbances", "异常扰动"),
    "extreme_conditions": ("extreme_conditions", "极限工况"),
    "participating_systems": ("participating_systems", "参与系统"),
    "system_scale": ("system_scale", "系统规模"),
    "applicable_boundary": ("applicable_boundary", "适用边界"),
    "affected_object": ("affected_object", "影响对象"),
    "business_impact": ("business_impact", "业务影响"),
    "recovery_method": ("recovery_method", "恢复方法", "问题处理结果_问题解决方案", "解决方案"),
    "experience_requirement": ("experience_requirement", "客户质量体验要求"),
    "quality_attribute": ("quality_attribute", "质量特性"),
    "quality_subcharacteristic": ("quality_subcharacteristic", "质量子特性"),
    "validation_direction": ("validation_direction", "测试验证方向"),
    "measurement_suggestion": ("measurement_suggestion", "建议度量"),
    "escape_reason": ("漏测分析_流出原因", "测试漏测原因", "漏测原因", "流出原因"),
}

DIMENSIONS: dict[str, tuple[str, ...]] = {
    "usage_context": (
        "product_code", "product_model", "product_line", "customer_name",
        "customer_industry", "user_type", "lifecycle_code", "activity_code",
        "software_usage_scenario",
    ),
    "failure_behavior": (
        "customer_perception", "symptom", "failure_mode", "failure_mechanism",
        "software_failure_mode", "software_failure_mechanism",
        "hardware_failure_mode", "hardware_failure_mechanism",
        "mechanical_failure_mode", "mechanical_failure_mechanism", "root_cause",
    ),
    "trigger_and_conditions": (
        "preconditions", "trigger_conditions", "operating_environment",
        "operating_condition", "duration_frequency", "disturbances",
        "extreme_conditions", "participating_systems", "system_scale",
        "applicable_boundary",
    ),
    "customer_impact": (
        "affected_object", "business_impact", "recovery_method",
    ),
    "quality_and_validation": (
        "experience_requirement", "quality_attribute",
        "quality_subcharacteristic", "validation_direction",
        "measurement_suggestion", "escape_reason",
    ),
}

# Explicitly domain-specific facts do NOT cross into an unrelated problem
# domain. A multi-domain CS may carry each separate failure type.
DOMAIN_FIELDS = {
    "software_failure_mode": "SOFTWARE",
    "software_failure_mechanism": "SOFTWARE",
    "hardware_failure_mode": "HARDWARE",
    "hardware_failure_mechanism": "HARDWARE",
    "mechanical_failure_mode": "MECHANICAL",
    "mechanical_failure_mechanism": "MECHANICAL",
}

# A software missed-test formal source can speak to software escape
# analysis. It does not override the CS's product/failure/impact facts.
MISSED_FIELDS = frozenset({
    "software_failure_mode", "software_failure_mechanism",
    "symptom", "escape_reason", "validation_direction",
    "measurement_suggestion",
})


def _scalar(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return ""
    return str(value).strip()


def _candidate_digest(plan: Mapping[str, Any]) -> str:
    ref = plan["entry_material"]
    source_keys = [
        (s["source_ref"], s["source_hash"], s["version_no"])
        for s in plan["formal_source_reads"]
    ]
    identity = (CONTRACT_VERSION, plan["entry_workbench"], ref["material_id"],
                ref["source_hash"], ref["version_no"], sorted(source_keys))
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]


def preview_one_issue(db_path: str, workbench: str, material_id: str) -> dict[str, Any]:
    """Return deterministic, non-persisted single-problem candidate preview."""
    plan = resolve_entry(db_path, workbench, material_id)
    if not plan["can_extract_facts"]:
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "BLOCKED",
            "blockers": [plan["status"]],
            "source_plan": plan,
            "candidate": None,
            "persisted": False,
            "human_confirmed": False,
        }

    domains = set(plan["domains"])
    sources = plan["formal_source_reads"]
    values: dict[str, str] = {}
    evidence: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    all_field_evidence: dict[str, list[dict[str, Any]]] = {}

    with read_only_db(db_path) as db:
        for source_ref in sources:
            source = _source(db, source_ref["material_id"])
            if (source["version_no"] != source_ref["version_no"] or
                    source["source_hash"] != source_ref["source_hash"] or
                    source["material_type"] != source_ref["material_type"]):
                raise EntryGateError("SOURCE_IDENTITY_CHANGED")
            if source["material_type"] not in ("ITR_CS", "ESCAPE_ANALYSIS"):
                raise EntryGateError("NON_FORMAL_SOURCE_IN_READ_PLAN")
            if (plan["problem_ref_context"] and source.get("canonical_itr") and
                    source["canonical_itr"].strip().upper() !=
                    plan["problem_ref_context"].strip().upper()):
                raise EntryGateError("SOURCE_CONTEXT_MISMATCH")
            raw = source["raw"]
            for field, aliases in FIELD_ALIASES.items():
                required_domain = DOMAIN_FIELDS.get(field)
                if required_domain and required_domain not in domains:
                    continue
                if source["material_type"] == "ESCAPE_ANALYSIS" and field not in MISSED_FIELDS:
                    continue
                if source["material_type"] == "ITR_CS" and field == "escape_reason":
                    continue
                matched = next(
                    ((name, _scalar(raw.get(name))) for name in aliases
                     if _scalar(raw.get(name))), None
                )
                if matched is None:
                    continue
                raw_key, value = matched
                locator = (
                    f"material://{quote(source['material_id'], safe='')}"
                    f"?version={source['version_no']}#/raw_json/"
                    f"{quote(raw_key.replace('~', '~0').replace('/', '~1'), safe='')}"
                )
                item = {
                    "field": field, "value": value,
                    "source_type": source["material_type"],
                    "material_id": source["material_id"],
                    "source_hash": source["source_hash"],
                    "source_revision": source["version_no"],
                    "raw_field": raw_key, "locator": locator,
                    "provenance": "ORIGINAL_SOURCE_FIELD",
                    "evidence_kind": "FACT",
                }
                all_field_evidence.setdefault(field, []).append(item)
                if field in values:
                    if values[field] != value:
                        conflicts.append({
                            "field": field, "left": evidence[field],
                            "right": item, "status": "REVIEW_REQUIRED",
                        })
                else:
                    values[field] = value
                    evidence[field] = item

    dims = {
        dim: {field: values[field] for field in fields if field in values}
        for dim, fields in DIMENSIONS.items()
    }
    missing = [
        field for field in (
            "lifecycle_code", "activity_code", "user_type",
            "experience_requirement", "trigger_conditions",
            "validation_direction",
        ) if field not in values
    ]
    if not any(field in values for field in (
        "symptom", "failure_mode", "software_failure_mode",
        "hardware_failure_mode", "mechanical_failure_mode",
    )):
        missing.append("failure_symptom_or_mode")
    blockers = ["HUMAN_REVIEW_REQUIRED", "LIFECYCLE_ACTIVITY_TAXONOMY_UNVERIFIED"]
    if conflicts:
        blockers.append("SOURCE_FIELD_CONFLICT")
    if missing:
        blockers.append("MISSING_STRUCTURED_FACTS")
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "CANDIDATE_PREVIEW",
        "candidate": {
            "preview_id": "QS-PREVIEW-" + _candidate_digest(plan),
            "problem_ref_context": plan["problem_ref_context"],
            "entry_workbench": plan["entry_workbench"],
            "problem_domains": plan["domains"],
            "source_coverage": plan["source_coverage"],
            "source_refs": sources,
            "dimensions": dims,
            "legacy_compatible_fields": values,
            "field_evidence": evidence,
            "all_field_evidence": all_field_evidence,
            "conflicts": conflicts,
            "missing_information": missing,
            "review": {
                "status": "PENDING",
                "confirmation_questions": [
                    f"请核实或补充：{field}" for field in missing
                ] + (["请人工判断同名字段的不同来源是否冲突"] if conflicts else []),
            },
            "status": "PENDING_HUMAN_REVIEW",
            "publish_ready": False,
        },
        "blockers": blockers,
        "persisted": False,
        "human_confirmed": False,
    }


def create_candidate_preview_router(db_path: str) -> APIRouter:
    """Sandbox-only opt-in router; requires authorization review before product mount."""
    router = APIRouter(tags=["QS Next Candidate Preview"])

    @router.get("/api/v2/qs-next/candidate/v1/{workbench}/{material_id}")
    def preview(workbench: str, material_id: str) -> dict[str, Any]:
        try:
            return preview_one_issue(db_path, workbench, material_id)
        except EntryGateError as exc:
            raise HTTPException(exc.status, detail=exc.code) from exc

    return router


__all__ = [
    "CONTRACT_VERSION", "FIELD_ALIASES", "DIMENSIONS", "DOMAIN_FIELDS",
    "preview_one_issue", "create_candidate_preview_router",
]
