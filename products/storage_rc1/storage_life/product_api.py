from __future__ import annotations

from typing import Any
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

from . import ai, core, templates, parameter_baseline
from .knowledge_release import KnowledgeReleaseConsumer

UI_STATUS = {
    "FOUND": "FOUND",
    "NOT_SPECIFIED": "NOT_FOUND",
    "NOT_APPLICABLE": "NOT_APPLICABLE",
    "UNRESOLVED": "NOT_CHECKED",
}

UX_CONFIRMED = "CONFIRMED"
UX_TRUSTED = "TRUSTED"
UX_NEEDS_ATTENTION = "NEEDS_ATTENTION"
UX_UNKNOWN = "UNKNOWN"
UX_NOT_APPLICABLE = "NOT_APPLICABLE"

UX_LABELS = {
    UX_CONFIRMED: "已确认",
    UX_TRUSTED: "可信，可批量确认",
    UX_NEEDS_ATTENTION: "需要处理",
    UX_UNKNOWN: "无法从规格书确定",
    UX_NOT_APPLICABLE: "不适用",
}

DIAG_DATASHEET_EXPLICIT = "DATASHEET_EXPLICIT"
DIAG_STANDARD_REQUIRES_VALIDATION = "STANDARD_APPLICABLE_REQUIRES_DEVICE_VALIDATION"
DIAG_DATASHEET_NOT_DECLARED = "DATASHEET_NOT_DECLARED"
DIAG_EXPLICITLY_NOT_SUPPORTED = "EXPLICITLY_NOT_SUPPORTED"
DIAG_NOT_APPLICABLE = "NOT_APPLICABLE"
DIAG_KNOWLEDGE_GAP = "KNOWLEDGE_GAP"

DIAGNOSTIC_LABELS = {
    DIAG_DATASHEET_EXPLICIT: "规格书明确",
    DIAG_STANDARD_REQUIRES_VALIDATION: "标准知识适用，待实机验证",
    DIAG_DATASHEET_NOT_DECLARED: "规格书未声明",
    DIAG_EXPLICITLY_NOT_SUPPORTED: "明确不支持",
    DIAG_NOT_APPLICABLE: "不适用",
    DIAG_KNOWLEDGE_GAP: "知识缺口",
}

DIAGNOSTIC_METHODS = {
    "life_time_a": ("EXT_CSD", "读取 DEVICE_LIFE_TIME_EST_TYP_A", "按 JEDEC/厂商定义解释寿命区间"),
    "life_time_b": ("EXT_CSD", "读取 DEVICE_LIFE_TIME_EST_TYP_B", "结合对应存储区域解释寿命区间"),
    "pre_eol": ("EXT_CSD", "读取 PRE_EOL_INFO", "按正常/预警/紧急状态解释预留块风险"),
    "device_life_time_est_typ_a": ("EXT_CSD", "读取 DEVICE_LIFE_TIME_EST_TYP_A / EXT_CSD[268]", "按 JEDEC/厂商定义解释寿命区间"),
    "device_life_time_est_typ_b": ("EXT_CSD", "读取 DEVICE_LIFE_TIME_EST_TYP_B / EXT_CSD[269]", "结合对应存储区域解释寿命区间"),
    "pre_eol_info": ("EXT_CSD", "读取 PRE_EOL_INFO / EXT_CSD[267]", "按正常/预警/紧急状态解释预留块风险"),
    "bkops_status": ("EXT_CSD", "读取 BKOPS_STATUS / EXT_CSD[246]", "判断后台维护需求与长期运行风险"),
    "ext_csd_health_report": ("EXT_CSD", "读取设备健康字段", "与 Life Time A/B、PRE_EOL 联合判断"),
    "bkops": ("EXT_CSD", "读取 BKOPS_STATUS / BKOPS_EN", "判断后台维护需求与长期运行风险"),
    "percentage_used": ("NVMe SMART / Health", "读取 Percentage Used", "作为耐久消耗估计，不能单独替代厂商寿命模型"),
    "data_units_written": ("NVMe SMART / Health", "读取 Data Units Written 并换算主机写入量", "与 TBW/工作负载联合评估"),
    "smart_health": ("SMART / NVMe Health", "读取健康日志", "结合 Critical Warning、Media Errors、温度等联合判断"),
    "media_errors": ("SMART / NVMe Health", "读取介质错误计数", "持续增长需结合日志与业务负载分析"),
    "critical_warning": ("NVMe SMART / Health", "读取 Critical Warning 位", "任一关键告警均进入人工诊断"),
    "available_spare": ("NVMe SMART / Health", "读取 Available Spare / Threshold", "低于阈值进入寿命风险关注"),
    "available_spare_threshold": ("NVMe SMART / Health", "读取 Available Spare Threshold", "仅与同次 Available Spare 观测比较，不单独形成诊断"),
    "ecc_status": ("器件状态寄存器/驱动统计", "读取 ECC corrected/uncorrectable 状态", "观察纠错压力与不可纠正错误趋势"),
    "bit_flip_count": ("NAND Controller / ECC 统计", "读取检测到的 Bit Flip / Corrected Bit 数量", "单次非零不直接判故障；结合 ECC 能力、阈值和时间趋势判断裕量变化"),
    "bit_flip_threshold": ("Datasheet / Controller 阈值", "读取同次采集提供的 Bit Flip / ECC 告警阈值", "仅与同次 Bit Flip 观测比较，不单独形成诊断"),
    "runtime_bad_block": ("MTD/UBI/驱动统计", "读取运行期坏块数量与增长", "坏块增长需结合擦写分布和 ECC 判断"),
    "read_retry": ("NAND Controller / 驱动统计", "统计 Read Retry 触发", "频繁触发提示读取裕量下降"),
    "program_fail": ("状态寄存器/驱动日志", "统计 Program Fail", "重复失败进入介质/电源/时序诊断"),
    "erase_fail": ("状态寄存器/驱动日志", "统计 Erase Fail", "重复失败进入坏块与磨损诊断"),
    "lifetime_counter": ("厂商寄存器", "按 Datasheet 读取寿命计数", "严格按厂商定义解释"),
}

IMPACT_RULES = {
    "pe_cycles": ("写入耐久边界变化", "重新核对写入预算、合并写/缓存策略", "增加寿命/高频写场景验证", "复核擦写计数或寿命监控"),
    "tbw": ("产品级累计写入耐久变化", "重新计算软件写入预算和寿命裕量", "按目标工作负载重跑耐久测试", "复核 Data Units Written / Percentage Used"),
    "dwpd": ("持续写入耐久能力变化", "复核长期写入节流与容量规划", "增加持续写压力场景", "增加日写入量监控"),
    "life_time_a": ("eMMC 寿命区间定义/状态变化", "复核健康状态解释与告警策略", "验证 EXT_CSD 读取与边界值", "监控 Life Time A"),
    "life_time_b": ("eMMC 区域寿命状态变化", "复核区域映射和告警策略", "验证对应区域寿命字段", "监控 Life Time B"),
    "pre_eol": ("预留块/EOL 风险能力变化", "复核预警和降级策略", "验证 PRE_EOL 各状态", "监控 PRE_EOL"),
    "ecc_capability": ("纠错能力变化", "复核错误处理和数据恢复策略", "增加 ECC 边界/不可纠正错误测试", "监控 corrected/uncorrectable 错误"),
    "smart_health": ("健康遥测能力变化", "复核健康采集与告警接口", "验证 SMART/NVMe Health 采集", "监控健康日志"),
    "percentage_used": ("寿命消耗遥测变化", "复核寿命估算与告警阈值", "验证字段读取/边界解释", "监控 Percentage Used"),
}


def _coverage_states(device_id: str) -> dict[str, dict[str, Any]]:
    run = core.get_extraction_run(device_id) or {}
    states = ((run.get("coverage") or {}).get("states") or [])
    return {str(x.get("field_key") or ""): x for x in states if isinstance(x, dict)}


def _candidate_map(device_id: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in core.list_candidates(device_id):
        result.setdefault(str(item.get("canonical_name") or ""), []).append(item)
    return result


def _coverage_status(coverage: dict[str, Any] | None) -> str:
    state = str((coverage or {}).get("state") or "UNRESOLVED")
    reason = str((coverage or {}).get("reason") or "").lower()
    if state == "UNRESOLVED" and ("ambiguous" in reason or "conflict" in reason):
        return "AMBIGUOUS"
    return UI_STATUS.get(state, "NOT_CHECKED")


def _review_status(candidates: list[dict[str, Any]]) -> str:
    if any(x.get("verify_status") == "confirmed" for x in candidates):
        return "CONFIRMED"
    if any(x.get("verify_status") == "pending" for x in candidates):
        return "UNREVIEWED"
    if candidates and all(x.get("verify_status") == "rejected" for x in candidates):
        return "REJECTED"
    return "UNREVIEWED" if candidates else "NOT_REVIEWED"


def _slot_status(candidates: list[dict[str, Any]], coverage: dict[str, Any] | None) -> str:
    review = _review_status(candidates)
    if review in {"CONFIRMED", "UNREVIEWED", "REJECTED"}:
        return review
    return _coverage_status(coverage)


def _explicitly_not_supported(value: Any) -> bool:
    if value is False:
        return True
    marker = str(value or "").strip().casefold()
    return marker in {
        "unsupported", "not supported", "not-supported", "no support",
        "false", "不支持", "明确不支持", "不具备",
    }


def _diagnostic_semantics(
    *,
    field: dict[str, Any],
    coverage_status: str,
    primary: dict[str, Any] | None,
    evidence: list[dict[str, Any]],
    formal_knowledge: dict[str, Any],
) -> dict[str, Any] | None:
    """Classify diagnostic meaning without collapsing datasheet, knowledge and runtime layers."""
    if field.get("group") != parameter_baseline.KEY_DIAGNOSTIC:
        return None
    if coverage_status == "NOT_APPLICABLE":
        status = DIAG_NOT_APPLICABLE
    else:
        direct = bool(coverage_status == "FOUND" and primary and evidence)
        candidate_value = (primary or {}).get("final_value")
        if candidate_value is None:
            candidate_value = (primary or {}).get("ai_value")
        if direct and _explicitly_not_supported(candidate_value):
            status = DIAG_EXPLICITLY_NOT_SUPPORTED
        elif direct:
            status = DIAG_DATASHEET_EXPLICIT
        elif formal_knowledge.get("status") == "MATCHED":
            status = DIAG_STANDARD_REQUIRES_VALIDATION
        elif formal_knowledge.get("status") == "NO_MATCH" and coverage_status == "NOT_FOUND":
            status = DIAG_DATASHEET_NOT_DECLARED
        else:
            status = DIAG_KNOWLEDGE_GAP
    return {
        "status": status,
        "label": DIAGNOSTIC_LABELS[status],
        "datasheet_layer": coverage_status,
        "knowledge_layer": formal_knowledge.get("status"),
        "runtime_layer": "REQUIRES_OBSERVATION"
        if status == DIAG_STANDARD_REQUIRES_VALIDATION else "NOT_EVALUATED",
    }


def _coverage_metrics(slots: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(slots)
    search_known = sum(
        1 for x in slots
        if x.get("coverage_status") in {"FOUND", "NOT_FOUND", "NOT_APPLICABLE"}
    )
    applicable = [x for x in slots if x.get("coverage_status") != "NOT_APPLICABLE"]
    confirmed = [
        x for x in applicable
        if x.get("review_status") == "CONFIRMED" and x.get("value") is not None
    ]
    return {
        "search_coverage_ratio": round(search_known / total, 4) if total else 0,
        "fact_coverage_ratio": round(len(confirmed) / len(applicable), 4) if applicable else 0,
        "search_known_count": search_known,
        "search_total_count": total,
        "confirmed_fact_count": len(confirmed),
        "applicable_fact_count": len(applicable),
    }


def _engineering_result_view(skill_result: dict[str, Any]) -> dict[str, Any]:
    structured = dict(skill_result.get("structured_result") or {})
    separation = dict(skill_result.get("fact_derived_hypothesis_separation") or {})
    missing = list(skill_result.get("missing_information") or [])
    validation = (
        structured.get("validation_requirements")
        or structured.get("validation_method")
        or structured.get("suggested_validation")
        or []
    )
    if not isinstance(validation, list):
        validation = [validation]
    return {
        "source_skill_id": skill_result.get("skill_id"),
        "status": skill_result.get("status"),
        "direct_answer": skill_result.get("direct_answer"),
        "facts": separation.get("facts") or [],
        "formal_knowledge": skill_result.get("knowledge_refs") or [],
        "derived_result": structured,
        "hypotheses": separation.get("hypotheses") or [],
        "unknowns": missing or separation.get("unknowns") or [],
        "evidence_refs": skill_result.get("evidence_refs") or [],
        "validation_requirements": validation,
        "next_action": (
            "补齐缺失的 Formal Knowledge / Runtime Observation / 证据后重新评估"
            if missing
            else "进入人工工程评审，不自动形成替代或寿命决策"
        ),
        "decision_boundary": skill_result.get("decision_boundary"),
    }


def _knowledge_release_identity() -> dict[str, Any]:
    status = KnowledgeReleaseConsumer.current().status()
    return {
        "available": bool(status.get("available")),
        "knowledge_release_version": str(status.get("knowledge_release_version") or ""),
        "snapshot_hash": str(status.get("snapshot_hash") or ""),
    }


def _device_fact_fingerprint(detail: dict[str, Any]) -> str:
    """Stable identity for the currently consumable Device Fact set.

    Downstream S2-S5 assessments store this fingerprint so a later S1 fact/evidence
    change cannot silently leave stale results marked as current.
    """
    rows = []
    for fact in detail.get("device_facts") or []:
        evidence = [
            {
                "evidence_id": e.get("evidence_id"),
                "source_id": e.get("source_id"),
                "source_page": e.get("source_page"),
                "source_section": e.get("source_section"),
            }
            for e in (fact.get("evidence") or [])
        ]
        rows.append({
            "canonical_name": fact.get("canonical_name"),
            "value": fact.get("value"),
            "unit": fact.get("unit"),
            "condition": fact.get("condition"),
            "scope": fact.get("scope"),
            "evidence": evidence,
        })
    payload = json.dumps(
        sorted(rows, key=lambda x: str(x.get("canonical_name") or "")),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _runtime_trend_fingerprint(device_id: str) -> str:
    """Stable identity for the formally consumable runtime trend state."""
    trend = core.runtime_metric_trends(device_id, limit=40)
    payload = []
    for metric in trend.get("metrics") or []:
        for point in metric.get("points") or []:
            payload.append({
                "metric_name": metric.get("metric_name"),
                "batch_id": point.get("batch_id"),
                "captured_at": point.get("captured_at"),
                "normalized_value": point.get("normalized_value"),
                "unit": point.get("unit"),
                "source_label": point.get("source_label"),
            })
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(raw.encode("utf-8")).hexdigest()


LIFETIME_FORMAL_KNOWLEDGE_QUERIES = {
    "NVME_PERCENTAGE_USED_INTERPRETATION_V1": {
        "query": "NVMe Percentage Used",
        "semantic_tokens": ("percentage used", "percentage_used"),
    },
    "NVME_DATA_UNITS_WRITTEN_V1": {
        "query": "NVMe Data Units Written bytes per data unit",
        "semantic_tokens": ("data units written", "data_units_written", "bytes_per_data_unit"),
    },
    "EMMC_DEVICE_LIFE_TIME_A_V1": {
        "query": "eMMC DEVICE_LIFE_TIME_EST_TYP_A",
        "semantic_tokens": ("device_life_time_est_typ_a", "life time a", "life_time_a"),
    },
    "EMMC_DEVICE_LIFE_TIME_B_V1": {
        "query": "eMMC DEVICE_LIFE_TIME_EST_TYP_B",
        "semantic_tokens": ("device_life_time_est_typ_b", "life time b", "life_time_b"),
    },
    "EMMC_PRE_EOL_V1": {
        "query": "eMMC PRE_EOL_INFO",
        "semantic_tokens": ("pre_eol_info", "pre eol", "pre_eol"),
    },
}


def _formal_lifetime_knowledge(requested_metric: str, device_type: str) -> list[dict[str, Any]]:
    """Map only PACK_LIFETIME_ENGINEERING released objects into LifetimeEngine.

    Public Knowledge/RAG is deliberately excluded.  The existing Skill Pack
    performs release-version/object-type/evidence filtering first.  Protocol
    parameters are copied only from explicit structured fields; prose is never
    parsed into constants.
    """
    from .lifetime_engine import FormulaRegistry
    from skills.real_knowledge import RealKnowledgeAssessmentService

    formal_metric = FormulaRegistry.canonicalize(requested_metric)
    config = LIFETIME_FORMAL_KNOWLEDGE_QUERIES.get(formal_metric)
    if not config:
        return []

    service = RealKnowledgeAssessmentService.current()
    result = service.adapter.query_pack(
        "PACK_LIFETIME_ENGINEERING",
        config["query"],
        device_type=device_type,
        top_k=8,
    )
    if result.get("status") != "READY":
        return []

    release_version = str(result.get("knowledge_release_version") or "")
    refs: list[dict[str, Any]] = []
    for obj in result.get("items") or []:
        evidence_refs = [
            str(x).strip()
            for x in (obj.get("evidence_refs") or [])
            if str(x).strip()
        ]
        if not evidence_refs:
            continue

        semantic_text = " ".join([
            str(obj.get("title") or ""),
            str(obj.get("summary") or ""),
            str(obj.get("content") or ""),
            " ".join(str(x) for x in (obj.get("tags") or [])),
            " ".join(str(x) for x in (obj.get("scope") or [])),
        ]).lower()
        if not any(token in semantic_text for token in config["semantic_tokens"]):
            continue

        parameters: dict[str, Any] = {}
        explicit_parameters = obj.get("parameters")
        if isinstance(explicit_parameters, dict):
            parameters.update(explicit_parameters)
        structured_content = obj.get("content")
        if isinstance(structured_content, dict):
            nested_parameters = structured_content.get("parameters")
            if isinstance(nested_parameters, dict):
                parameters.update(nested_parameters)

        semantic_scope = " ".join(
            str(x).strip()
            for x in [
                obj.get("title"),
                *(obj.get("scope") or []),
                *(obj.get("tags") or []),
            ]
            if str(x or "").strip()
        )
        knowledge_id = str(obj.get("object_id") or "")
        object_release_version = str(obj.get("knowledge_release_version") or release_version)
        if not knowledge_id or not object_release_version:
            continue
        refs.append({
            "knowledge_id": knowledge_id,
            "release_version": object_release_version,
            "release_status": "RELEASED",
            "semantic_scope": semantic_scope or formal_metric,
            "evidence_refs": evidence_refs,
            "parameters": parameters,
        })
    return refs

def _safe_lifetime_facts(detail: dict[str, Any]) -> list[dict[str, Any]]:
    dtype = templates.normalize_device_type(detail["device"]["device_type"])
    mapping = {
        ("SSD", "tbw"): "rated_tbw_bytes",
        ("SSD", "capacity"): "capacity_bytes",
        ("NAND Flash", "pe_cycles"): "rated_pe_cycles",
        ("NOR Flash", "pe_cycles"): "rated_endurance_cycles",
    }
    result = []
    for fact in detail.get("device_facts") or []:
        metric_name = mapping.get((dtype, fact.get("canonical_name")))
        if not metric_name:
            continue
        value = fact.get("value")
        unit = str(fact.get("unit") or "").strip()
        try:
            float(str(value).strip())
        except (TypeError, ValueError):
            continue
        evidence_refs = [
            str(e.get("evidence_id") or e.get("source_id") or "").strip()
            for e in fact.get("evidence") or []
            if str(e.get("evidence_id") or e.get("source_id") or "").strip()
        ]
        if not unit or not evidence_refs:
            continue
        result.append({
            "fact_id": f'{detail["device"]["id"]}:{fact.get("canonical_name")}',
            "metric_name": metric_name,
            "value": value,
            "unit": unit,
            "scope": fact.get("scope") or None,
            "condition": fact.get("condition") or None,
            "evidence_refs": evidence_refs,
            "source_type": "CONFIRMED_DEVICE_FACT",
        })
    return result


def _enrich_evidence(device: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for raw in evidence or []:
        item = dict(raw)
        item.setdefault("source_filename", device.get("filename"))
        item.setdefault("document_number", device.get("document_number"))
        item.setdefault("revision", device.get("revision"))
        item.setdefault("revision_date", device.get("revision_date"))
        enriched.append(item)
    return enriched

def _device_lifecycle(device_id: str) -> dict[str, Any]:
    workflow = core.specification_workflow_status(device_id)
    return {
        "status": "FORMAL_READY" if workflow.get("formal_ready") else "DRAFT",
        "formal_ready": bool(workflow.get("formal_ready")),
        "workflow_status": workflow.get("status"),
        "reason": (
            "HUMAN_CONFIRMED_DEVICE_FACT_READY"
            if workflow.get("formal_ready")
            else "REVIEW_REQUIRED"
        ),
    }


def _require_formal_device(device_id: str) -> dict[str, Any]:
    lifecycle = _device_lifecycle(device_id)
    if not lifecycle["formal_ready"]:
        raise ValueError("DEVICE_NOT_FORMAL_READY")
    return lifecycle


def _formal_knowledge(
    canonical_name: str,
    parameter_name: str,
    device_type: str,
    *,
    context: str = "",
    top_k: int = 3,
) -> dict[str, Any]:
    consumer = KnowledgeReleaseConsumer.current()
    status = consumer.status()
    if not status.get("available"):
        return {
            "status": "UNKNOWN",
            "code": status.get("code") or "KNOWLEDGE_RELEASE_NOT_READY",
            "knowledge_release_version": None,
            "results": [],
            "evidence_refs": [],
        }
    query = " ".join(
        item
        for item in (canonical_name, parameter_name, context)
        if str(item or "").strip()
    )
    try:
        result = consumer.query(
            query,
            device_type=device_type,
            top_k=top_k,
        )
    except Exception as exc:
        return {
            "status": "UNKNOWN",
            "code": str(exc),
            "knowledge_release_version": status.get("knowledge_release_version"),
            "results": [],
            "evidence_refs": [],
        }
    rows = result.get("results") or []
    evidence_refs = []
    for row in rows:
        for evidence in row.get("evidence") or []:
            evidence_id = evidence.get("evidence_id")
            if evidence_id and evidence_id not in evidence_refs:
                evidence_refs.append(evidence_id)
    return {
        "status": "MATCHED" if rows else "NO_MATCH",
        "code": None if rows else "NO_MATCHING_PUBLISHED_KNOWLEDGE",
        "knowledge_release_version": result.get("knowledge_release_version"),
        "results": rows,
        "evidence_refs": evidence_refs,
    }


def device_slots(device_id: str) -> dict[str, Any]:
    devices = {x["id"]: x for x in core.list_devices()}
    if device_id not in devices:
        raise KeyError(device_id)
    device = devices[device_id]
    coverage = _coverage_states(device_id)
    candidates = _candidate_map(device_id)
    fields = parameter_baseline.product_fields(
        device["device_type"], ai.expected_fields(device["device_type"], device.get("vendor", ""))
    )
    slots = []
    for field in fields:
        key = field["canonical_name"]
        aliases = field.get("aliases") or [key]
        items = [item for alias in aliases for item in candidates.get(alias, [])]
        coverage_item = next((coverage.get(alias) for alias in aliases if coverage.get(alias)), None)
        confirmed = next((x for x in items if x.get("verify_status") == "confirmed"), None)
        pending = next((x for x in items if x.get("verify_status") == "pending"), None)
        rejected = next((x for x in items if x.get("verify_status") == "rejected"), None)
        primary = confirmed or pending or rejected
        review_status = _review_status(items)
        coverage_status = _coverage_status(coverage_item)
        status = _slot_status(items, coverage_item)
        evidence = []
        if primary:
            evidence = primary.get("evidence") or [{
                "source_page": primary.get("source_page"),
                "source_section": primary.get("source_section"),
                "source_text": primary.get("source_text"),
                "confidence": primary.get("confidence"),
            }]
        evidence = _enrich_evidence(device, evidence)
        is_diagnostic = field.get("group") == parameter_baseline.KEY_DIAGNOSTIC
        formal_knowledge = (
            _formal_knowledge(
                key,
                field.get("parameter_name") or key,
                device["device_type"],
                context="engineering meaning diagnostic lifetime",
            )
            if is_diagnostic or review_status == "CONFIRMED"
            else {
                "status": "NOT_APPLICABLE",
                "code": "DEVICE_FACT_NOT_CONFIRMED",
                "knowledge_release_version": None,
                "results": [],
                "evidence_refs": [],
            }
        )
        diagnostic_semantics = _diagnostic_semantics(
            field=field,
            coverage_status=coverage_status,
            primary=primary,
            evidence=evidence,
            formal_knowledge=formal_knowledge,
        )
        # ``value`` is the formal Device Fact surface: never expose an AI candidate here.
        formal_value = (confirmed or {}).get("final_value") if confirmed else None
        formal_unit = (confirmed or {}).get("final_unit") if confirmed else ""
        slots.append({
            "canonical_name": key,
            "parameter_name": field.get("parameter_name") or key,
            "priority": "P0" if field.get("requirement_level") == "MUST" else "P1",
            "group": field.get("group") or parameter_baseline.COMPREHENSIVE,
            "group_label": field.get("group_label") or parameter_baseline.GROUP_LABELS[parameter_baseline.COMPREHENSIVE],
            "requirement_level": field.get("requirement_level") or "FULL",
            "status": status,
            "coverage_status": coverage_status,
            "review_status": review_status,
            "value": formal_value,
            "unit": formal_unit,
            "ai_value": (primary or {}).get("ai_value"),
            "ai_unit": (primary or {}).get("ai_unit"),
            "human_value": (primary or {}).get("final_value"),
            "human_unit": (primary or {}).get("final_unit"),
            "condition": (confirmed or {}).get("condition") or "",
            "scope": (confirmed or {}).get("scope") or "",
            "candidate_condition": (primary or {}).get("condition") or "",
            "candidate_scope": (primary or {}).get("scope") or "",
            "verified_by": (primary or {}).get("verified_by"),
            "verified_at": (primary or {}).get("verified_at"),
            "knowledge": field.get("note") or "",
            "role": field.get("role") or "",
            "evidence": evidence,
            "candidate_id": (primary or {}).get("id"),
            "formal_knowledge": formal_knowledge,
            "diagnostic_semantics": diagnostic_semantics,
            "diagnostic_status": (diagnostic_semantics or {}).get("status"),
            "diagnostic_label": (diagnostic_semantics or {}).get("label"),
        })
    states = ("CONFIRMED", "UNREVIEWED", "REJECTED", "NOT_FOUND", "NOT_CHECKED", "AMBIGUOUS", "NOT_APPLICABLE")
    counts = {state: sum(1 for x in slots if x["status"] == state) for state in states}
    coverage_metrics = _coverage_metrics(slots)
    facts = [
        {k: slot.get(k) for k in ("canonical_name", "parameter_name", "value", "unit", "condition", "scope", "evidence", "verified_by", "verified_at")}
        for slot in slots if slot["review_status"] == "CONFIRMED"
    ]
    return {
        "device": device,
        "slots": slots,
        "device_facts": facts,
        "counts": counts,
        # Backward-compatible alias: coverage_ratio remains SEARCH_COVERAGE.
        "coverage_ratio": coverage_metrics["search_coverage_ratio"],
        "search_coverage_ratio": coverage_metrics["search_coverage_ratio"],
        "fact_coverage_ratio": coverage_metrics["fact_coverage_ratio"],
        "coverage_model": {
            "SEARCH_COVERAGE": {
                "ratio": coverage_metrics["search_coverage_ratio"],
                "known": coverage_metrics["search_known_count"],
                "total": coverage_metrics["search_total_count"],
            },
            "FACT_COVERAGE": {
                "ratio": coverage_metrics["fact_coverage_ratio"],
                "confirmed": coverage_metrics["confirmed_fact_count"],
                "applicable": coverage_metrics["applicable_fact_count"],
            },
        },
        "workflow": core.specification_workflow_status(device_id),
        "lifecycle": _device_lifecycle(device_id),
        "conclusion": core.get_device_conclusion(device_id),
    }


def add_manual_device_fact(device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    devices = {x["id"]: x for x in core.list_devices()}
    if device_id not in devices:
        raise KeyError(device_id)
    device = devices[device_id]
    requested = str(payload.get("canonical_name") or "").strip()
    if not requested:
        raise ValueError("CANONICAL_NAME_REQUIRED")

    fields = parameter_baseline.product_fields(
        device["device_type"], ai.expected_fields(device["device_type"], device.get("vendor", ""))
    )
    target = None
    for field in fields:
        aliases = [field.get("canonical_name"), *(field.get("aliases") or [])]
        if requested in {str(x) for x in aliases if x}:
            target = field
            break
    if target is None:
        raise ValueError(f"UNKNOWN_DEVICE_FIELD:{requested}")

    fact = core.add_manual_fact(
        device_id,
        target["canonical_name"],
        target.get("parameter_name") or target["canonical_name"],
        payload.get("value"),
        payload.get("unit"),
        payload.get("verified_by"),
        source_page=payload.get("source_page"),
        source_text=payload.get("source_text"),
        source_section=payload.get("source_section") or "",
        condition=payload.get("condition") or "",
        scope=payload.get("scope") or "",
    )
    detail = device_slots(device_id)
    slot = next(
        (x for x in detail["slots"] if x.get("canonical_name") == target["canonical_name"]),
        None,
    )
    return {
        "fact": fact,
        "slot": slot,
        "workflow": detail["workflow"],
        "lifecycle": detail["lifecycle"],
        "conclusion": detail["conclusion"],
    }


def save_runtime_snapshot(device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    devices = {x["id"]: x for x in core.list_devices()}
    if device_id not in devices:
        raise KeyError(device_id)

    source_label = str(payload.get("source_label") or "").strip()
    captured_at = payload.get("captured_at")
    observations = []
    for raw in list(payload.get("observations") or []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        source_line = str(item.get("source_line") or item.get("evidence_ref") or "").strip()
        user_confirmed = item.get("confirmed_by_user") is True
        # Formal trend eligibility is issued at the server boundary.  Browser
        # supplied quality_status / availability_status are ignored.
        provenance_ready = bool(
            user_confirmed
            and captured_at
            and source_label
            and source_label != "PASTED_RUNTIME_OUTPUT"
            and source_line
        )
        item["quality_status"] = "VALID" if provenance_ready else "UNKNOWN"
        item["availability_status"] = "AVAILABLE" if captured_at else "NOT_AVAILABLE"
        item["confirmed_by_user"] = provenance_ready
        observations.append(item)

    return core.save_runtime_snapshot(
        device_id,
        observations,
        source_label=source_label or "PASTED_RUNTIME_OUTPUT",
        raw_text=str(payload.get("raw_text") or ""),
        captured_at=captured_at,
        created_by=str(payload.get("created_by") or "Storage MVP UI"),
    )


def runtime_snapshot_history(device_id: str, limit: int = 20) -> dict[str, Any]:
    devices = {x["id"]: x for x in core.list_devices()}
    if device_id not in devices:
        raise KeyError(device_id)
    return {
        "device": devices[device_id],
        "items": core.list_runtime_snapshots(device_id, limit=limit),
        "trend": core.runtime_metric_trends(device_id, limit=max(limit, 40)),
    }


def device_assessment_history(device_id: str, limit: int = 20) -> dict[str, Any]:
    devices = {x["id"]: x for x in core.list_devices()}
    if device_id not in devices:
        raise KeyError(device_id)
    items = core.list_device_assessments(device_id, limit=limit)
    return {
        "device": devices[device_id],
        "count": len(items),
        "items": items,
    }


def _is_supporting_lifetime_assessment(item: dict[str, Any] | None) -> bool:
    if not item:
        return False
    request = item.get("input") or {}
    metric = str(request.get("requested_metric") or "")
    return metric in {
        "NVME_DATA_UNITS_WRITTEN_V1",
        "nvme.data_units_written",
        "GENERIC_WAF_V1",
        "generic.waf",
    }


def _latest_product_assessments(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Select the newest product assessment per scenario.

    A conversion-only Lifetime record must not hide an earlier user-facing
    lifetime/risk assessment created in the same workflow.
    """
    latest: dict[str, dict[str, Any]] = {}
    for item in items:
        kind = str(item.get("assessment_type") or "").upper()
        if not kind:
            continue
        if kind not in latest:
            latest[kind] = item
            continue
        if (
            kind == "LIFETIME"
            and _is_supporting_lifetime_assessment(latest[kind])
            and not _is_supporting_lifetime_assessment(item)
        ):
            latest[kind] = item
    return latest


def device_mvp_summary(device_id: str) -> dict[str, Any]:
    """Compose the current Storage MVP state into one user-facing device summary.

    This function does not create a new conclusion engine. It only assembles
    confirmed Device Facts, the existing device conclusion, and the latest
    persisted Lifetime / Diagnosis / Optimization assessments.
    """
    detail = device_slots(device_id)
    assessments = core.list_device_assessments(device_id, limit=50)
    latest = _latest_product_assessments(assessments)

    runtime_trend = core.runtime_metric_trends(device_id, limit=40)
    latest_formal_runtime_at = _iso_datetime(runtime_trend.get("latest_formal_capture_time"))
    latest_formal_snapshot_created_at = _iso_datetime(runtime_trend.get("latest_formal_snapshot_created_at"))
    current_knowledge_release = _knowledge_release_identity()
    current_runtime_trend_fingerprint = _runtime_trend_fingerprint(device_id)
    completed_statuses = {"ANSWERED", "CALCULATED", "READY", "COMPLETED", "CONFIRMED"}
    current_fact_fingerprint = _device_fact_fingerprint(detail)

    def scenario(kind: str, label: str) -> dict[str, Any]:
        item = latest.get(kind)
        if not item:
            return {
                "type": kind,
                "label": label,
                "status": "NOT_RUN",
                "complete": False,
                "assessment_id": None,
                "created_at": None,
                "direct_answer": None,
                "next_action": None,
            }
        result = item.get("result") or {}
        skill = result.get("skill_result") or {}
        engineering = result.get("engineering_result") or {}
        status = str(item.get("status") or skill.get("status") or "UNKNOWN").upper()
        complete = status in completed_statuses
        next_action = engineering.get("next_action")
        structured = skill.get("structured_result") or {}
        recorded_input = item.get("input") or {}
        recorded_knowledge_release = dict(recorded_input.get("_knowledge_release_identity") or {})
        if recorded_knowledge_release != current_knowledge_release:
            complete = False
            next_action = "Formal Knowledge Release 已变化；请基于当前发布知识重新执行该场景。"

        if kind == "COMPARE":
            current_new = current_fact_fingerprint
            recorded_new = str(recorded_input.get("_new_device_fact_fingerprint") or "")
            old_id = str(recorded_input.get("old_id") or "")
            recorded_old = str(recorded_input.get("_old_device_fact_fingerprint") or "")
            old_current = ""
            old_formal_ready = False
            if old_id:
                try:
                    old_detail = device_slots(old_id)
                    old_current = _device_fact_fingerprint(old_detail)
                    old_formal_ready = bool((old_detail.get("lifecycle") or {}).get("formal_ready"))
                except KeyError:
                    old_current = ""
            new_formal_ready = bool((detail.get("lifecycle") or {}).get("formal_ready"))
            compare_unknowns = list(result.get("unknowns") or [])
            low_confidence = any(
                str(x.get("confidence") or "").upper() != "EVIDENCED"
                for x in (result.get("items") or [])
            )
            if (
                not recorded_new
                or not recorded_old
                or recorded_new != current_new
                or recorded_old != old_current
            ):
                complete = False
                next_action = "S1 Device Fact 已变化或基准器件不可用；请基于当前事实重新执行 S2 参数差异与影响。"
            elif not old_formal_ready or not new_formal_ready:
                complete = False
                next_action = "S2 需要新旧器件都达到 FORMAL_READY；请先完成两侧 S1 参数事实确认。"
            elif compare_unknowns or low_confidence:
                complete = False
                next_action = "S2 仍存在未确认/缺失事实；请消除待验证项后重新执行参数差异与影响。"
        else:
            recorded_fact_fingerprint = str(recorded_input.get("_device_fact_fingerprint") or "")
            if not recorded_fact_fingerprint or recorded_fact_fingerprint != current_fact_fingerprint:
                complete = False
                next_action = "S1 Device Fact 已变化；请基于当前正式事实重新执行该场景。"

        if kind == "LIFETIME":
            requested_metric = str((item.get("input") or {}).get("requested_metric") or "")
            supporting_only = {"NVME_DATA_UNITS_WRITTEN_V1", "nvme.data_units_written", "GENERIC_WAF_V1", "generic.waf"}
            if requested_metric in supporting_only:
                complete = False
                next_action = "继续执行寿命消耗 / 裕量 / 健康解释类评估；当前记录仅为支撑计算。"
            elif requested_metric in {"SSD_DWPD_OBSERVED_V1", "ssd.dwpd"}:
                projection = structured.get("target_service_life_projection") or {}
                if projection.get("budget_status") not in {"WITHIN_BUDGET", "EXCEEDS_BUDGET"}:
                    complete = False
                    next_action = "补充目标服役寿命和已确认 TBW，使实际 DWPD 能形成目标寿命预算判断。"
        elif kind == "OPTIMIZATION":
            workload = recorded_input.get("workload_software_facts") or []
            has_behavior = any(
                str(x.get("description") or "").strip()
                for x in workload
                if isinstance(x, dict)
            )
            controls = structured.get("engineering_control_options") or []
            validation = structured.get("suggested_validation") or []
            chain_context = dict((recorded_input.get("user_context") or {}).get("assessment_context") or {})
            chain_lifetime = dict(chain_context.get("latest_lifetime") or {})
            chain_diagnosis = dict(chain_context.get("latest_diagnosis") or {})
            current_lifetime = latest.get("LIFETIME") or {}
            current_diagnosis = latest.get("DIAGNOSIS") or {}
            current_lifetime_id = current_lifetime.get("id")
            current_diagnosis_id = current_diagnosis.get("id")
            current_lifetime_status = str(current_lifetime.get("status") or "").upper()
            current_diagnosis_status = str(current_diagnosis.get("status") or "").upper()
            chain_current = bool(
                current_lifetime_id and current_diagnosis_id
                and chain_lifetime.get("assessment_id") == current_lifetime_id
                and chain_diagnosis.get("assessment_id") == current_diagnosis_id
                and current_lifetime_status in {"ANSWERED", "CALCULATED"}
                and current_diagnosis_status == "ANSWERED"
                and str(chain_lifetime.get("status") or "").upper() == current_lifetime_status
                and str(chain_diagnosis.get("status") or "").upper() == current_diagnosis_status
            )
            if not has_behavior:
                complete = False
                next_action = "补充当前软件写入 / 日志 / 持久化行为后重新生成针对性优化建议。"
            elif not chain_current:
                complete = False
                next_action = "基于最新 S3 寿命结果和 S4 诊断结果重新生成综合优化方案。"
            elif status in completed_statuses and not controls:
                complete = False
                next_action = "当前优化结果没有形成可执行软件控制；补充软件行为/风险上下文或正式知识后重新生成。"
            elif status in completed_statuses and not validation:
                complete = False
                next_action = "当前优化结果缺少独立测试/验证方法；补齐 DiagnosticMethod / 验证知识后重新生成，不能把软件控制项复制成测试项。"
        elif kind == "DIAGNOSIS":
            current_observation = structured.get("current_observation") or []
            diagnosis_status = str(structured.get("diagnosis_status") or "")
            signals = structured.get("abnormality_signal") or []
            missing_information = list(skill.get("missing_information") or structured.get("missing_information") or [])
            if not current_observation:
                complete = False
                next_action = "提供可正式消费的当前运行观测后重新执行诊断。"
            elif diagnosis_status == "ABNORMAL_SIGNAL_PRESENT" and signals:
                # Explicit deterministic signals are a valid S4 diagnosis even
                # when richer mechanism knowledge is still missing.
                complete = status == "ANSWERED"
            elif diagnosis_status == "NO_REGISTERED_SIGNAL":
                semantic_gaps = [
                    str(x) for x in missing_information
                    if any(token in str(x) for token in (
                        "NO_MATCHING_RELEASED_KNOWLEDGE",
                        "FORMAL_DIAGNOSTIC_KNOWLEDGE",
                        "INSUFFICIENT_KNOWLEDGE",
                    ))
                ]
                if semantic_gaps:
                    complete = False
                    next_action = "当前观测未触发确定性异常，但相关正式诊断语义仍不完整；补齐 Formal Knowledge 后重新判读。"
            else:
                complete = False
                next_action = "当前运行观测尚未形成可完成的 S4 诊断结果。"

        if kind in {"LIFETIME", "DIAGNOSIS", "OPTIMIZATION"} and latest_formal_snapshot_created_at:
            assessment_created_at = _iso_datetime(item.get("created_at"))
            if assessment_created_at is None or latest_formal_snapshot_created_at > assessment_created_at:
                complete = False
                next_action = (
                    "已确认 Runtime Snapshot 集合在本次分析后发生变化；请基于当前快照重新执行该场景。"
                    if kind != "OPTIMIZATION"
                    else "已确认 Runtime Snapshot 集合在本次优化后发生变化；请先刷新 S3/S4，再重新生成 S5。"
                )

        if kind in {"LIFETIME", "DIAGNOSIS", "OPTIMIZATION"}:
            recorded_runtime_fingerprint = str(recorded_input.get("_runtime_trend_fingerprint") or "")
            if (
                not recorded_runtime_fingerprint
                or recorded_runtime_fingerprint != current_runtime_trend_fingerprint
            ):
                complete = False
                next_action = (
                    "已确认 Runtime Snapshot / Trend 已变化；请基于当前运行数据重新执行该场景。"
                    if kind != "OPTIMIZATION"
                    else "已确认 Runtime Snapshot / Trend 已变化；请先刷新 S3/S4，再重新生成 S5 优化方案。"
                )

        if kind in {"LIFETIME", "DIAGNOSIS", "OPTIMIZATION"} and latest_formal_runtime_at:
            recorded_capture_times = []
            if kind in {"LIFETIME", "DIAGNOSIS"}:
                recorded_capture_times = [
                    _iso_datetime(x.get("capture_time"))
                    for x in (recorded_input.get("runtime_observations") or [])
                    if isinstance(x, dict) and x.get("capture_time")
                ]
            else:
                recorded_runtime_context = (
                    ((recorded_input.get("user_context") or {}).get("assessment_context") or {})
                    .get("runtime_context") or []
                )
                recorded_capture_times = [
                    _iso_datetime(x.get("captured_at"))
                    for x in recorded_runtime_context
                    if isinstance(x, dict) and x.get("captured_at")
                ]
            recorded_capture_times = [x for x in recorded_capture_times if x is not None]
            latest_recorded_capture = max(recorded_capture_times) if recorded_capture_times else None
            if latest_recorded_capture is None:
                complete = False
                next_action = (
                    "当前历史评估没有绑定可追溯的运行采集时间；请基于当前 Runtime Snapshot 重新执行。"
                    if kind != "OPTIMIZATION"
                    else "当前历史优化没有绑定可追溯的 Runtime 上下文；请先刷新 S3/S4，再重新生成 S5。"
                )
            elif latest_formal_runtime_at > latest_recorded_capture:
                complete = False
                next_action = (
                    "存在采集时间更新的已确认 Runtime Snapshot；请基于最新运行数据重新执行该场景。"
                    if kind != "OPTIMIZATION"
                    else "存在采集时间更新的已确认 Runtime Snapshot；请先刷新 S3/S4，再重新生成 S5 优化方案。"
                )
        return {
            "type": kind,
            "label": label,
            "status": status,
            "complete": complete,
            "assessment_id": item.get("id"),
            "created_at": item.get("created_at"),
            "direct_answer": engineering.get("direct_answer") or skill.get("direct_answer"),
            "next_action": next_action,
        }

    facts = detail.get("device_facts") or []
    slots = detail.get("slots") or []
    applicable = [x for x in slots if x.get("coverage_status") != "NOT_APPLICABLE"]
    missing_critical = list((detail.get("conclusion") or {}).get("missing_critical_fields") or [])
    lifecycle_ready = bool((detail.get("lifecycle") or {}).get("formal_ready"))
    fact_status = "READY" if lifecycle_ready else ("PARTIAL" if facts else "NOT_RUN")
    fact_scenario = {
        "type": "FACT",
        "label": "S1 参数事实",
        "status": fact_status,
        "complete": lifecycle_ready,
        "assessment_id": None,
        "created_at": None,
        "direct_answer": (
            f"已确认 {len(facts)} 项 Device Fact"
            if facts else "尚无已确认 Device Fact"
        ),
        "next_action": (
            "补齐关键 Device Fact"
            if missing_critical else
            "完成剩余 P0/P1 参数确认并达到 FORMAL_READY"
            if facts and not lifecycle_ready else
            None
        ),
    }
    scenario_items = [
        fact_scenario,
        scenario("COMPARE", "S2 参数差异与影响"),
        scenario("LIFETIME", "S3 寿命 / 风险"),
        scenario("DIAGNOSIS", "S4 运行诊断"),
        scenario("OPTIMIZATION", "S5 软件优化"),
    ]
    remaining = []
    if missing_critical:
        remaining.append(f"补齐关键 Device Fact：{'、'.join(str(x) for x in missing_critical[:8])}")
    elif facts and not lifecycle_ready:
        remaining.append("完成剩余 P0/P1 参数确认，使 S1 达到 FORMAL_READY")
    for item in scenario_items:
        if item["type"] == "FACT":
            continue
        if not item.get("complete"):
            if item["status"] == "NOT_RUN":
                remaining.append(f"执行{item['label']}")
            else:
                remaining.append(f"{item['label']} 尚未形成可完成结果：{item['status']}")
    action_items = core.list_engineering_actions(device_id)
    return {
        "device": detail["device"],
        "lifecycle": detail["lifecycle"],
        "conclusion": detail["conclusion"],
        "facts": {
            "confirmed": len(facts),
            "applicable": len(applicable),
            "fact_coverage_ratio": detail.get("fact_coverage_ratio") or 0,
            "search_coverage_ratio": detail.get("search_coverage_ratio") or 0,
            "missing_critical_fields": missing_critical,
        },
        "scenarios": scenario_items,
        "runtime": {
            "snapshot_count": runtime_trend.get("snapshot_count") or 0,
            "formal_snapshot_count": runtime_trend.get("formal_snapshot_count") or 0,
            "metric_count": sum(1 for x in (runtime_trend.get("metrics") or []) if x.get("latest")),
            "raw_metric_count": len(runtime_trend.get("metrics") or []),
            "unverified_metric_count": sum(
                1 for x in (runtime_trend.get("metrics") or [])
                if not x.get("latest") and (x.get("raw_sample_count") or 0) > 0
            ),
            "latest_metrics": [
                {
                    "metric_name": x.get("metric_name"),
                    "sample_count": x.get("sample_count"),
                    "latest": x.get("latest"),
                    "delta": x.get("delta"),
                }
                for x in (runtime_trend.get("metrics") or [])
                if x.get("latest")
            ][:12],
            "interpretation_performed": False,
            "formal_trend_only": bool(runtime_trend.get("formal_trend_only")),
        },
        "action_checklist": {
            "items": action_items,
            "open_count": sum(
                1 for x in action_items
                if x.get("status") in {"OPEN", "IN_PROGRESS"}
            ),
        },
        "public_knowledge": {
            "integration": "IN_CONTEXT",
            "role": "ENGINEERING_CONTEXT_AND_CITATION",
            "formal_evidence": False,
            "storage_diagnosis": False,
        },
        "remaining_actions": remaining,
        "mvp_ready_for_demo": all(bool(x.get("complete")) for x in scenario_items),
    }


def confirmed_device_facts(device_id: str) -> dict[str, Any]:
    detail = device_slots(device_id)
    return {
        "device": detail["device"],
        "workflow": detail["workflow"],
        "fact_count": len(detail["device_facts"]),
        "facts": detail["device_facts"],
        "gate": "CONFIRMED_ONLY",
    }


def _bind_runtime_observations(
    device_id: str,
    device_type: str,
    observations: list[dict[str, Any]],
    *,
    trusted_runtime: bool = False,
) -> list[dict[str, Any]]:
    """Bind client/runtime observations to the selected device at the server boundary.

    Browser/client claims such as quality_status=VALID are not trusted by
    themselves.  A client observation becomes formally consumable only when it
    explicitly confirms provenance and supplies capture/source/evidence.  Server
    derived trend observations use trusted_runtime=True after the snapshot store
    has already enforced those requirements.
    """
    raw_items = list(observations or [])
    if len(raw_items) > 128:
        raise ValueError("RUNTIME_OBSERVATION_LIMIT_EXCEEDED:128")
    bound: list[dict[str, Any]] = []
    normalized_type = templates.normalize_device_type(device_type)
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("RUNTIME_OBSERVATION_OBJECT_REQUIRED")
        item = dict(raw)
        claimed_device = str(item.get("device_id") or "").strip()
        if claimed_device and claimed_device != device_id:
            raise ValueError("RUNTIME_OBSERVATION_DEVICE_MISMATCH")
        claimed_type = str(item.get("device_type") or "").strip()
        if claimed_type and templates.normalize_device_type(claimed_type) != normalized_type:
            raise ValueError("RUNTIME_OBSERVATION_DEVICE_TYPE_MISMATCH")

        capture_time = item.get("capture_time")
        capture_dt = _iso_datetime(capture_time)
        if capture_time and (capture_dt is None or capture_dt.tzinfo is None):
            raise ValueError("RUNTIME_CAPTURE_TIME_TIMEZONE_REQUIRED")
        source = str(item.get("source_command_or_interface") or "").strip()
        evidence = str(item.get("evidence_ref") or item.get("raw_output_ref") or "").strip()
        placeholder_sources = {"PASTED_RUNTIME_OUTPUT", "UNKNOWN", "N/A", "NA"}
        source_is_explicit = bool(source and source.upper() not in placeholder_sources)
        if len(source) > 500:
            raise ValueError("RUNTIME_SOURCE_LABEL_TOO_LONG")
        if len(evidence) > 4000:
            raise ValueError("RUNTIME_EVIDENCE_REF_TOO_LONG")
        metric_name = str(item.get("metric_name") or "").strip()
        if not metric_name or len(metric_name) > 160:
            raise ValueError("RUNTIME_METRIC_NAME_INVALID")
        confirmed = item.pop("confirmed_by_user", False) is True
        provenance_ready = bool(capture_time and source_is_explicit and evidence and (trusted_runtime or confirmed))

        item["device_id"] = device_id
        item["device_type"] = normalized_type
        item["metric_name"] = metric_name
        item["source_command_or_interface"] = source or None
        item["evidence_ref"] = evidence or None
        item["raw_output_ref"] = str(item.get("raw_output_ref") or evidence or "").strip() or None
        # Drop caller-supplied derived trust claims and re-issue them server-side.
        item.pop("is_formally_consumable", None)
        item["quality_status"] = "VALID" if provenance_ready else "UNKNOWN"
        item["availability_status"] = "AVAILABLE" if capture_time else "NOT_AVAILABLE"
        bound.append(item)
    return bound


def execute_device_skill(
    device_id: str,
    skill_id: str,
    payload: dict[str, Any] | None = None,
    *,
    trusted_runtime: bool = False,
) -> dict[str, Any]:
    """Bind one selected device to the existing four Storage Domain Skill contracts."""
    allowed = {
        "storage-write-governance",
        "storage-lifetime-budget",
        "storage-diagnostic-validation",
        "storage-change-impact",
    }
    if skill_id not in allowed:
        raise ValueError(f"UNKNOWN_STORAGE_SKILL:{skill_id}")

    detail = device_slots(device_id)
    request = dict(payload or {})
    dtype = templates.normalize_device_type(detail["device"]["device_type"])
    bound_runtime = _bind_runtime_observations(
        device_id,
        dtype,
        list(request.get("runtime_observations") or []),
        trusted_runtime=trusted_runtime,
    )
    request["runtime_observations"] = bound_runtime
    context = {
        "device_id": device_id,
        "device_type": dtype,
        "confirmed_device_facts": detail.get("device_facts") or [],
        "knowledge_release": KnowledgeReleaseConsumer.current().status(),
        "runtime_observations": bound_runtime,
    }

    from skills.real_knowledge import RealKnowledgeAssessmentService
    service = RealKnowledgeAssessmentService.current()

    if skill_id == "storage-write-governance":
        user_context = dict(request.get("user_context") or {})
        user_context.setdefault("question", f"{dtype} software write behavior lifetime governance")
        user_context["device_context"] = context
        skill_payload = {
            "device_type": dtype,
            "user_context": user_context,
            "workload_software_facts": list(request.get("workload_software_facts") or []),
        }
    elif skill_id == "storage-diagnostic-validation":
        capabilities = list(request.get("diagnostic_capabilities") or [])
        if not capabilities:
            capabilities = [
                {
                    "canonical_name": x["canonical_name"],
                    "diagnostic_status": x.get("diagnostic_status"),
                    "datasheet_fact": x.get("value"),
                    "review_status": x.get("review_status"),
                    "evidence_refs": [
                        e.get("evidence_id") or e.get("source_id")
                        for e in x.get("evidence") or []
                        if e.get("evidence_id") or e.get("source_id")
                    ],
                }
                for x in detail["slots"]
                if x.get("group") == parameter_baseline.KEY_DIAGNOSTIC
            ]
        skill_payload = {
            "device_type": dtype,
            "target_question": str(
                request.get("target_question")
                or f"{dtype} diagnostic capability validation and runtime observation requirements"
            ),
            "diagnostic_capabilities": capabilities,
            "runtime_observations": list(request.get("runtime_observations") or []),
        }
    elif skill_id == "storage-change-impact":
        skill_payload = {
            "device_type": dtype,
            "parameter_delta": list(request.get("parameter_delta") or []),
            "question": str(request.get("question") or "device change impact"),
        }
    else:
        requested_metric = str(request.get("requested_metric") or "").strip()
        if not requested_metric:
            raise ValueError("REQUESTED_METRIC_REQUIRED")
        assessment = dict(request.get("assessment_request") or {})
        assessment["device_id"] = device_id
        # Product-side formal semantics are always rebound to the current
        # server-validated Knowledge Release.  Client-supplied RELEASED flags or
        # protocol parameters are not trusted across this boundary.
        assessment["formal_knowledge"] = _formal_lifetime_knowledge(requested_metric, dtype)
        # Confirmed Device Facts are a server-owned boundary.  Do not merge
        # caller-provided objects that merely claim source_type=CONFIRMED_DEVICE_FACT.
        assessment["confirmed_facts"] = _safe_lifetime_facts(detail)
        # Nested caller-provided observations are never trusted over the product
        # boundary; only the server-bound top-level observations are consumed.
        assessment["runtime_observations"] = bound_runtime
        skill_payload = {
            "assessment_request": assessment,
            "requested_metric": requested_metric,
            "target_service_life": dict(request.get("target_service_life") or {}),
        }

    result = service.execute_skill(skill_id, skill_payload)
    response = {
        "device": detail["device"],
        "context": context,
        "skill_payload": skill_payload,
        "skill_result": result,
        "engineering_result": _engineering_result_view(result),
        "adapter": "EXISTING_STORAGE_DOMAIN_SKILL_ADAPTER",
        "second_skill_stack": False,
        "second_knowledge_stack": False,
    }
    if request.get("record_assessment") is True:
        assessment_type = {
            "storage-lifetime-budget": "LIFETIME",
            "storage-diagnostic-validation": "DIAGNOSIS",
            "storage-write-governance": "OPTIMIZATION",
        }.get(skill_id)
        if assessment_type:
            record_input = dict(request)
            record_input["_device_fact_fingerprint"] = _device_fact_fingerprint(detail)
            record_input["_knowledge_release_identity"] = _knowledge_release_identity()
            record_input["_runtime_trend_fingerprint"] = _runtime_trend_fingerprint(device_id)
            response["assessment_record"] = core.save_device_assessment(
                device_id,
                assessment_type,
                result.get("status") if isinstance(result, dict) else "UNKNOWN",
                record_input,
                {
                    "skill_id": skill_id,
                    "skill_result": result,
                    "engineering_result": response["engineering_result"],
                },
                created_by=str(request.get("assessment_author") or "Storage MVP"),
            )
    return response


def _iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def analyze_runtime_trend(
    device_id: str,
    target_service_life: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn saved runtime snapshots into existing Lifetime Skill calls.

    No new lifetime formula is introduced here.  The function only derives
    explicit deltas/time windows from saved snapshots and feeds them into the
    already-registered Lifetime Engine metrics.
    """
    trend = core.runtime_metric_trends(device_id, limit=40)
    device = {x["id"]: x for x in core.list_devices()}.get(device_id)
    if not device:
        raise KeyError(device_id)
    dtype = templates.normalize_device_type(device["device_type"])
    results: list[dict[str, Any]] = []
    primary_target_assessment_recorded = False

    def observation(metric_name: str, point: dict[str, Any], value: Any | None = None, unit: str | None = None):
        return {
            "observation_id": f"trend-{point.get('batch_id')}-{metric_name}",
            "device_id": device_id,
            "device_type": dtype,
            "metric_name": metric_name,
            "raw_value": point.get("raw_value") if value is None else value,
            "normalized_value": point.get("normalized_value") if value is None else value,
            "unit": unit or point.get("unit") or "",
            "capture_time": point.get("captured_at"),
            "source_command_or_interface": point.get("source_label") or "SAVED_RUNTIME_SNAPSHOT",
            "raw_output_ref": point.get("source_line") or point.get("batch_id"),
            "evidence_ref": point.get("source_line") or point.get("batch_id"),
            "collector": "STORAGE_MVP_RUNTIME_TREND",
            # runtime_metric_trends() already filters to user-confirmed VALID/AVAILABLE
            # snapshot points. Re-assert the canonical RuntimeObservation contract
            # here so LifetimeEngine does not silently downgrade the derived point.
            "quality_status": "VALID",
            "availability_status": "AVAILABLE",
        }

    by_metric = {x["metric_name"]: x for x in trend.get("metrics") or []}

    # NVMe cumulative Data Units Written -> delta bytes -> observed DWPD.
    duw = by_metric.get("data_units_written")
    if dtype == "SSD" and duw and duw.get("previous") and duw.get("delta") is not None:
        latest, previous = duw["latest"], duw["previous"]
        start, end = _iso_datetime(previous.get("captured_at")), _iso_datetime(latest.get("captured_at"))
        elapsed_days = (end - start).total_seconds() / 86400 if start and end else None
        delta_units = float(duw["delta"])
        if elapsed_days and elapsed_days > 0 and delta_units >= 0:
            conversion = execute_device_skill(device_id, "storage-lifetime-budget", {
                "requested_metric": "NVME_DATA_UNITS_WRITTEN_V1",
                "runtime_observations": [
                    observation("data_units_written", latest, value=delta_units, unit=latest.get("unit") or "data_units")
                ],
                "assessment_request": {"assumptions": []},
                "target_service_life": {},
                "record_assessment": False,
                "assessment_author": "Storage MVP Runtime Trend",
            }, trusted_runtime=True)
            structured = (conversion.get("skill_result") or {}).get("structured_result") or {}
            converted = structured.get("measured_vs_budget") or {}
            host_bytes = converted.get("value") if isinstance(converted, dict) else None
            if host_bytes is not None:
                dwpd = execute_device_skill(device_id, "storage-lifetime-budget", {
                    "requested_metric": "SSD_DWPD_OBSERVED_V1",
                    "runtime_observations": [
                        observation("host_written_bytes", latest, value=host_bytes, unit="bytes")
                    ],
                    "assessment_request": {
                        "assumptions": [{
                            "name": "time_window_days",
                            "value": elapsed_days,
                            "unit": "days",
                            "rationale": "Derived from two saved runtime snapshot timestamps",
                            "evidence_refs": [previous.get("batch_id"), latest.get("batch_id")],
                        }]
                    },
                    "target_service_life": dict(target_service_life or {}),
                    "record_assessment": True,
                    "assessment_author": "Storage MVP Runtime Trend",
                }, trusted_runtime=True)
                projection = (
                    ((dwpd.get("skill_result") or {}).get("structured_result") or {})
                    .get("target_service_life_projection") or {}
                )
                primary_target_assessment_recorded = bool(
                    target_service_life
                    and projection.get("budget_status") in {"WITHIN_BUDGET", "EXCEEDS_BUDGET"}
                )
                results.append({
                    "kind": "TREND_DWPD",
                    "metric_name": "data_units_written",
                    "sample_count": duw.get("sample_count"),
                    "elapsed_days": elapsed_days,
                    "delta": delta_units,
                    "delta_unit": latest.get("unit") or "data_units",
                    "conversion": conversion,
                    "assessment": dwpd,
                    "primary_s3_assessment": primary_target_assessment_recorded,
                })

    direct_metrics = {
        "percentage_used": ("NVME_PERCENTAGE_USED_INTERPRETATION_V1", "percentage_used", "%"),
        "device_life_time_est_typ_a": ("EMMC_DEVICE_LIFE_TIME_A_V1", "device_life_time_a", "code"),
        "device_life_time_est_typ_b": ("EMMC_DEVICE_LIFE_TIME_B_V1", "device_life_time_b", "code"),
        "pre_eol_info": ("EMMC_PRE_EOL_V1", "pre_eol_info", "code"),
        "erase_count": ("NAND_ERASE_COUNT_MARGIN_V1", "erase_count", "cycles"),
        "pe_cycle": ("NAND_PE_MARGIN_V1", "pe_cycle", "cycles"),
    }
    for source_metric, (formal_metric, runtime_metric, default_unit) in direct_metrics.items():
        series = by_metric.get(source_metric)
        if not series or not series.get("latest"):
            continue
        point = series["latest"]
        try:
            assessment = execute_device_skill(device_id, "storage-lifetime-budget", {
                "requested_metric": formal_metric,
                "runtime_observations": [
                    observation(runtime_metric, point, unit=point.get("unit") or default_unit)
                ],
                "assessment_request": {"assumptions": []},
                "target_service_life": {},
                "record_assessment": not primary_target_assessment_recorded,
                "assessment_author": "Storage MVP Runtime Trend",
            }, trusted_runtime=True)
        except Exception as exc:
            results.append({
                "kind": "LATEST_SNAPSHOT",
                "metric_name": source_metric,
                "formal_metric": formal_metric,
                "error": str(exc),
            })
            continue
        results.append({
            "kind": "LATEST_SNAPSHOT",
            "metric_name": source_metric,
            "formal_metric": formal_metric,
            "sample_count": series.get("sample_count"),
            "delta": series.get("delta"),
            "assessment": assessment,
        })

    return {
        "device": device,
        "trend": trend,
        "results": results,
        "supported_result_count": len(results),
        "target_service_life": dict(target_service_life or {}),
        "new_formula_stack": False,
        "public_knowledge_used": False,
    }


def integrated_action_plan(device_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compose current Storage evidence into one optimization action plan.

    This is orchestration only: current-state facts come from persisted
    Lifetime/Diagnosis assessments and Runtime snapshots; engineering controls
    come from the existing storage-write-governance Skill.
    """
    request = dict(payload or {})
    detail = device_slots(device_id)
    assessments = core.list_device_assessments(device_id, limit=50)
    latest = _latest_product_assessments(assessments)

    trend = core.runtime_metric_trends(device_id, limit=40)
    current_fact_fingerprint = _device_fact_fingerprint(detail)
    current_knowledge_release = _knowledge_release_identity()
    current_runtime_trend_fingerprint = _runtime_trend_fingerprint(device_id)

    latest_formal_capture = _iso_datetime(trend.get("latest_formal_capture_time"))
    latest_formal_snapshot_created_at = _iso_datetime(trend.get("latest_formal_snapshot_created_at"))

    def assessment_view(kind: str) -> dict[str, Any] | None:
        item = latest.get(kind)
        if not item:
            return None
        recorded_input = item.get("input") or {}
        recorded_fact_fingerprint = str(recorded_input.get("_device_fact_fingerprint") or "")
        recorded_knowledge_release = dict(recorded_input.get("_knowledge_release_identity") or {})
        if not recorded_fact_fingerprint or recorded_fact_fingerprint != current_fact_fingerprint:
            return None
        if recorded_knowledge_release != current_knowledge_release:
            return None
        recorded_runtime_fingerprint = str(recorded_input.get("_runtime_trend_fingerprint") or "")
        if (
            kind in {"LIFETIME", "DIAGNOSIS"}
            and (
                not recorded_runtime_fingerprint
                or recorded_runtime_fingerprint != current_runtime_trend_fingerprint
            )
        ):
            return None
        if latest_formal_snapshot_created_at and kind in {"LIFETIME", "DIAGNOSIS"}:
            assessment_created_at = _iso_datetime(item.get("created_at"))
            if assessment_created_at is None or latest_formal_snapshot_created_at > assessment_created_at:
                return None
        if latest_formal_capture and kind in {"LIFETIME", "DIAGNOSIS"}:
            captures = [
                _iso_datetime(x.get("capture_time"))
                for x in (recorded_input.get("runtime_observations") or [])
                if isinstance(x, dict) and x.get("capture_time")
            ]
            captures = [x for x in captures if x is not None]
            if not captures or latest_formal_capture > max(captures):
                return None
        result = item.get("result") or {}
        skill = result.get("skill_result") or {}
        engineering = result.get("engineering_result") or {}
        structured = skill.get("structured_result") or {}
        status = str(item.get("status") or skill.get("status") or "UNKNOWN").upper()
        if kind == "LIFETIME":
            requested_metric = str(recorded_input.get("requested_metric") or "")
            if requested_metric in {"NVME_DATA_UNITS_WRITTEN_V1", "nvme.data_units_written", "GENERIC_WAF_V1", "generic.waf"}:
                return None
            if status not in {"ANSWERED", "CALCULATED"}:
                return None
            if requested_metric in {"SSD_DWPD_OBSERVED_V1", "ssd.dwpd"}:
                projection = structured.get("target_service_life_projection") or {}
                if projection.get("budget_status") not in {"WITHIN_BUDGET", "EXCEEDS_BUDGET"}:
                    return None
        elif kind == "DIAGNOSIS":
            current_observation = structured.get("current_observation") or []
            diagnosis_status = str(structured.get("diagnosis_status") or "")
            signals = structured.get("abnormality_signal") or []
            missing_information = list(skill.get("missing_information") or structured.get("missing_information") or [])
            if status != "ANSWERED" or not current_observation:
                return None
            if diagnosis_status == "ABNORMAL_SIGNAL_PRESENT" and signals:
                pass
            elif diagnosis_status == "NO_REGISTERED_SIGNAL":
                if any(
                    any(token in str(value) for token in (
                        "NO_MATCHING_RELEASED_KNOWLEDGE",
                        "FORMAL_DIAGNOSTIC_KNOWLEDGE",
                        "INSUFFICIENT_KNOWLEDGE",
                    ))
                    for value in missing_information
                ):
                    return None
            else:
                return None
        risk_context: dict[str, Any] = {}
        if kind == "LIFETIME":
            risk_context = {
                "margin_status": structured.get("margin_status"),
                "measured_vs_budget": structured.get("measured_vs_budget"),
                "target_service_life_projection": structured.get("target_service_life_projection"),
            }
        elif kind == "DIAGNOSIS":
            risk_context = {
                "diagnosis_status": structured.get("diagnosis_status"),
                "abnormality_signal": structured.get("abnormality_signal") or [],
            }
        runtime_evidence_refs = [
            str(x.get("evidence_ref") or x.get("raw_output_ref") or "").strip()
            for x in (structured.get("current_observation") or [])
            if isinstance(x, dict)
            and str(x.get("evidence_ref") or x.get("raw_output_ref") or "").strip()
        ]
        evidence_refs = sorted({
            str(ref)
            for ref in list(skill.get("evidence_refs") or []) + runtime_evidence_refs
            if str(ref)
        })
        return {
            "assessment_id": item.get("id"),
            "status": status,
            "created_at": item.get("created_at"),
            "direct_answer": engineering.get("direct_answer") or skill.get("direct_answer"),
            "next_action": engineering.get("next_action"),
            "missing_information": skill.get("missing_information") or [],
            "evidence_refs": evidence_refs,
            "knowledge_refs": skill.get("knowledge_refs") or [],
            "risk_context": risk_context,
        }

    lifetime = assessment_view("LIFETIME")
    diagnosis = assessment_view("DIAGNOSIS")
    software_behavior = str(request.get("software_behavior") or "").strip()

    context_terms = [
        "storage software write governance",
        "write amplification cache batching persistence logging wear lifetime optimization",
    ]
    if lifetime:
        context_terms.append(f"lifetime assessment status {lifetime['status']}")
    if diagnosis:
        context_terms.append(f"diagnosis assessment status {diagnosis['status']}")
    metric_names = [
        x.get("metric_name")
        for x in (trend.get("metrics") or [])
        if x.get("metric_name") and x.get("latest")
    ]
    runtime_context = [
        {
            "metric_name": x.get("metric_name"),
            "latest_value": (x.get("latest") or {}).get("normalized_value"),
            "unit": (x.get("latest") or {}).get("unit"),
            "captured_at": (x.get("latest") or {}).get("captured_at"),
            "delta": x.get("delta"),
            "sample_count": x.get("sample_count"),
        }
        for x in (trend.get("metrics") or [])
        if x.get("metric_name") and x.get("latest")
    ][:12]
    if metric_names:
        context_terms.append("runtime metrics " + " ".join(metric_names[:12]))

    workload_facts = []
    if software_behavior:
        workload_facts.append({
            "source": "USER_DECLARED_WORKLOAD",
            "description": software_behavior,
        })

    current_state = []
    if lifetime:
        current_state.append({"kind": "LIFETIME", **lifetime})
    if diagnosis:
        current_state.append({"kind": "DIAGNOSIS", **diagnosis})
    if metric_names:
        current_state.append({
            "kind": "RUNTIME_TREND",
            "snapshot_count": trend.get("snapshot_count") or 0,
            "metric_names": metric_names[:12],
            "runtime_context": runtime_context,
            "interpretation": "EXPLICIT_VALUE_TREND_ONLY",
        })

    blockers = []
    if not lifetime:
        blockers.append("S3 寿命 / 风险结果缺失、未完成或已过期")
    if not diagnosis:
        blockers.append("S4 运行诊断结果缺失、未完成或已过期")
    if not software_behavior:
        blockers.append("尚未提供当前软件写入 / 日志 / 持久化行为")
    if blockers:
        return {
            "device": detail["device"],
            "current_state": current_state,
            "engineering_controls": [],
            "potential_risks": [],
            "validation_actions": [],
            "conditions_and_limits": [],
            "remaining_information": blockers,
            "knowledge_refs": [],
            "evidence_refs": sorted({
                str(ref)
                for item in (lifetime, diagnosis)
                if item
                for ref in (item.get("evidence_refs") or [])
                if str(ref)
            }),
            "upstream_assessment_ids": [
                str(item.get("assessment_id"))
                for item in (lifetime, diagnosis)
                if item and item.get("assessment_id")
            ],
            "optimization_assessment": None,
            "action_checklist": None,
            "action_persistence_status": "BLOCKED_INCOMPLETE_CONTEXT",
            "skill_result": {
                "skill_id": "storage-write-governance",
                "status": "INSUFFICIENT_DATA",
                "direct_answer": "S5 需要当前有效的 S3、S4 结果和真实软件行为；当前上下文不足，未生成优化建议。",
                "missing_information": blockers,
                "structured_result": {},
            },
            "decision_boundary": "ENGINEERING_REVIEW_REQUIRED",
            "orchestration_only": True,
            "new_rule_stack": False,
        }

    optimization = execute_device_skill(device_id, "storage-write-governance", {
        "user_context": {
            "question": " ".join(context_terms),
            "assessment_context": {
                "latest_lifetime": lifetime,
                "latest_diagnosis": diagnosis,
                "runtime_snapshot_count": trend.get("snapshot_count") or 0,
                "runtime_metrics": metric_names[:12],
                "runtime_context": runtime_context,
            },
        },
        "workload_software_facts": workload_facts,
        "record_assessment": True,
        "assessment_author": str(request.get("assessment_author") or "Storage MVP Integrated Action Plan"),
    })
    skill = optimization.get("skill_result") or {}
    structured = skill.get("structured_result") or {}

    remaining = []
    if not lifetime:
        remaining.append("尚无寿命 / 风险评估记录")
    else:
        lifetime_status = str(lifetime.get("status") or "UNKNOWN").upper()
        if lifetime_status not in {"ANSWERED", "CALCULATED"}:
            remaining.append(f"寿命 / 风险评估尚未形成可执行结果：{lifetime_status}")
        remaining.extend(lifetime.get("missing_information") or [])
    if not diagnosis:
        remaining.append("尚无运行诊断记录")
    else:
        diagnosis_status = str(diagnosis.get("status") or "UNKNOWN").upper()
        if diagnosis_status != "ANSWERED":
            remaining.append(f"运行诊断尚未形成可执行结果：{diagnosis_status}")
        remaining.extend(diagnosis.get("missing_information") or [])
    if not software_behavior:
        remaining.append("尚未提供当前软件写入 / 日志 / 持久化行为")
    remaining.extend(skill.get("missing_information") or [])

    controls = structured.get("engineering_control_options") or []
    validation_actions = structured.get("suggested_validation") or []
    upstream_ready = bool(
        lifetime
        and diagnosis
        and software_behavior
        and str(skill.get("status") or "").upper() == "ANSWERED"
        and controls
        and validation_actions
    )
    combined_evidence_refs = sorted({
        str(ref)
        for ref in (
            list(skill.get("evidence_refs") or [])
            + list((lifetime or {}).get("evidence_refs") or [])
            + list((diagnosis or {}).get("evidence_refs") or [])
        )
        if str(ref)
    })
    combined_knowledge_refs = sorted({
        str(ref)
        for ref in (
            list(skill.get("knowledge_refs") or [])
            + list((lifetime or {}).get("knowledge_refs") or [])
            + list((diagnosis or {}).get("knowledge_refs") or [])
        )
        if str(ref)
    })
    upstream_assessment_ids = [
        str(x.get("assessment_id"))
        for x in (lifetime, diagnosis)
        if x and x.get("assessment_id")
    ]
    action_checklist = None
    action_persistence_status = "NOT_REQUESTED"
    if request.get("persist_actions") is True and upstream_ready:
        action_persistence_status = "PERSISTED"
        source_assessment_id = (optimization.get("assessment_record") or {}).get("id")
        action_rows = [
            {"action_type": "SOFTWARE_CONTROL", "title": str(x), "detail": str(x)}
            for x in controls if str(x).strip()
        ] + [
            {"action_type": "TEST_VALIDATION", "title": str(x), "detail": str(x)}
            for x in validation_actions if str(x).strip()
        ]
        if remaining:
            action_rows.extend(
                {"action_type": "FOLLOW_UP", "title": str(x), "detail": str(x)}
                for x in remaining if str(x).strip()
            )
        action_checklist = core.create_engineering_actions(
            device_id,
            source_assessment_id,
            action_rows,
            evidence_refs=combined_evidence_refs,
            knowledge_refs=combined_knowledge_refs,
            source_assessment_ids=upstream_assessment_ids,
            created_by=str(request.get("assessment_author") or "Storage MVP Integrated Action Plan"),
        )
    elif request.get("persist_actions") is True:
        action_persistence_status = "BLOCKED_INCOMPLETE_CONTEXT"

    return {
        "device": detail["device"],
        "current_state": current_state,
        "engineering_controls": controls,
        "potential_risks": structured.get("potential_risk") or [],
        "validation_actions": validation_actions,
        "conditions_and_limits": structured.get("conditions_and_limits") or [],
        "remaining_information": sorted({str(x) for x in remaining if str(x)}),
        "knowledge_refs": combined_knowledge_refs,
        "evidence_refs": combined_evidence_refs,
        "upstream_assessment_ids": upstream_assessment_ids,
        "optimization_assessment": optimization.get("assessment_record"),
        "upstream_ready": upstream_ready,
        "action_persistence_status": action_persistence_status,
        "action_checklist": action_checklist,
        "skill_result": skill,
        "decision_boundary": skill.get("decision_boundary") or "ENGINEERING_REVIEW_REQUIRED",
        "orchestration_only": True,
        "new_rule_stack": False,
    }


def persist_engineering_actions_from_optimization(
    device_id: str,
    assessment_id: str,
    *,
    updated_by: str = "Storage MVP UI",
) -> dict[str, Any]:
    """Persist the exact S5 plan the user already reviewed.

    The plan is not re-generated here.  This prevents "save" from silently
    executing the write-governance Skill a second time and changing the plan
    that the user just saw.
    """
    assessment = core.get_device_assessment(assessment_id)
    if assessment.get("device_id") != device_id:
        raise ValueError("OPTIMIZATION_ASSESSMENT_DEVICE_MISMATCH")
    if str(assessment.get("assessment_type") or "").upper() != "OPTIMIZATION":
        raise ValueError("OPTIMIZATION_ASSESSMENT_REQUIRED")

    summary = device_mvp_summary(device_id)
    s5 = next(
        (x for x in (summary.get("scenarios") or []) if x.get("type") == "OPTIMIZATION"),
        None,
    )
    if (
        not s5
        or s5.get("assessment_id") != assessment_id
        or not bool(s5.get("complete"))
    ):
        raise ValueError("OPTIMIZATION_ASSESSMENT_STALE_OR_INCOMPLETE")

    result = assessment.get("result") or {}
    skill = result.get("skill_result") or {}
    structured = skill.get("structured_result") or {}
    controls = [str(x) for x in (structured.get("engineering_control_options") or []) if str(x).strip()]
    validation = [str(x) for x in (structured.get("suggested_validation") or []) if str(x).strip()]
    missing = [str(x) for x in (skill.get("missing_information") or []) if str(x).strip()]
    if not controls and not validation:
        raise ValueError("OPTIMIZATION_ACTIONS_EMPTY")

    recorded_input = assessment.get("input") or {}
    chain_context = dict((recorded_input.get("user_context") or {}).get("assessment_context") or {})
    upstream_ids = [
        str((chain_context.get("latest_lifetime") or {}).get("assessment_id") or "").strip(),
        str((chain_context.get("latest_diagnosis") or {}).get("assessment_id") or "").strip(),
    ]
    upstream_ids = [x for x in upstream_ids if x]

    evidence_refs = {str(x) for x in (skill.get("evidence_refs") or []) if str(x)}
    knowledge_refs = {str(x) for x in (skill.get("knowledge_refs") or []) if str(x)}
    for upstream_id in upstream_ids:
        try:
            upstream = core.get_device_assessment(upstream_id)
        except KeyError:
            raise ValueError("UPSTREAM_ASSESSMENT_NOT_FOUND")
        if upstream.get("device_id") != device_id:
            raise ValueError("UPSTREAM_ASSESSMENT_DEVICE_MISMATCH")
        upstream_skill = ((upstream.get("result") or {}).get("skill_result") or {})
        evidence_refs.update(str(x) for x in (upstream_skill.get("evidence_refs") or []) if str(x))
        knowledge_refs.update(str(x) for x in (upstream_skill.get("knowledge_refs") or []) if str(x))
        structured_upstream = upstream_skill.get("structured_result") or {}
        for obs in structured_upstream.get("current_observation") or []:
            if not isinstance(obs, dict):
                continue
            ref = str(obs.get("evidence_ref") or obs.get("raw_output_ref") or "").strip()
            if ref:
                evidence_refs.add(ref)

    action_rows = [
        {"action_type": "SOFTWARE_CONTROL", "title": x, "detail": x}
        for x in controls
    ] + [
        {"action_type": "TEST_VALIDATION", "title": x, "detail": x}
        for x in validation
    ]
    action_rows.extend(
        {"action_type": "FOLLOW_UP", "title": x, "detail": x}
        for x in missing
    )

    checklist = core.create_engineering_actions(
        device_id,
        assessment_id,
        action_rows,
        evidence_refs=sorted(evidence_refs),
        knowledge_refs=sorted(knowledge_refs),
        source_assessment_ids=upstream_ids,
        created_by=updated_by,
    )
    return {
        "device_id": device_id,
        "optimization_assessment_id": assessment_id,
        "action_persistence_status": "PERSISTED",
        "action_checklist": checklist,
        "reexecuted_skill": False,
    }


def engineering_action_checklist(device_id: str) -> dict[str, Any]:
    devices = {x["id"]: x for x in core.list_devices()}
    if device_id not in devices:
        raise KeyError(device_id)
    items = core.list_engineering_actions(device_id)
    summary = {
        "open": sum(1 for x in items if x["status"] == "OPEN"),
        "in_progress": sum(1 for x in items if x["status"] == "IN_PROGRESS"),
        "done": sum(1 for x in items if x["status"] == "DONE"),
        "waived": sum(1 for x in items if x["status"] == "WAIVED"),
    }
    return {"device": devices[device_id], "summary": summary, "items": items}


def update_engineering_action(action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return core.update_engineering_action(
        action_id,
        payload.get("status"),
        updated_by=str(payload.get("updated_by") or "Storage MVP UI"),
    )


def _review_ux_state(row: dict[str, Any]) -> dict[str, Any]:
    """Compose coverage + review into one deterministic user-facing state.

    Coverage and raw review state remain available as advanced diagnostics.  This
    classifier is the single backend source for the normal Review UX so the browser
    never re-implements eligibility rules.
    """
    coverage_status = str(row.get("coverage_status") or "NOT_CHECKED")
    coverage_reason = str(row.get("coverage_reason") or "").lower()
    review_status = str(row.get("review_status") or "NOT_REVIEWED")
    requirement_level = str(row.get("requirement_level") or "FULL")
    candidate_id = row.get("candidate_id")
    ai_value = row.get("ai_value")
    human_value = row.get("human_value")
    value = human_value if review_status == "CONFIRMED" and human_value is not None else ai_value
    has_value = bool(str(value or "").strip())
    evidence_valid = bool(row.get("persistent_evidence"))
    reasons: list[str] = []

    if review_status == "CONFIRMED" and has_value and evidence_valid:
        # Human-confirmed facts are formal even when the extraction/search layer
        # originally reported NOT_FOUND. Keep SEARCH_COVERAGE unchanged, but do
        # not let it hide a later manual confirmation from the product workflow.
        state = UX_CONFIRMED
    elif coverage_status == "NOT_APPLICABLE":
        state = UX_NOT_APPLICABLE
        reasons.append("outside_device_profile")
    elif coverage_status == "NOT_FOUND":
        state = UX_UNKNOWN
        reasons.append("no_supported_value")
    elif not candidate_id:
        state = UX_NEEDS_ATTENTION
        reasons.append("mandatory_missing" if requirement_level == "MUST" else "no_candidate")
    elif review_status == "REJECTED":
        state = UX_NEEDS_ATTENTION
        reasons.append("rejected")
    elif coverage_status == "AMBIGUOUS" or "ambiguous" in coverage_reason or "conflict" in coverage_reason:
        state = UX_NEEDS_ATTENTION
        reasons.append("ambiguity_or_conflict")
    elif coverage_status != "FOUND":
        state = UX_NEEDS_ATTENTION
        reasons.append("unchecked")
    elif "stale" in coverage_reason:
        state = UX_NEEDS_ATTENTION
        reasons.append("stale")
    elif not has_value:
        state = UX_NEEDS_ATTENTION
        reasons.append("missing_value")
    elif not evidence_valid:
        state = UX_NEEDS_ATTENTION
        reasons.append("no_evidence")
    elif review_status == "CONFIRMED":
        state = UX_CONFIRMED
    else:
        state = UX_TRUSTED

    batch_confirmable = bool(state == UX_TRUSTED and candidate_id)
    critical_visible = bool(
        requirement_level == "MUST"
        and row.get("group") in {parameter_baseline.KEY_SPEC, parameter_baseline.KEY_DIAGNOSTIC}
    )
    return {
        "ux_state": state,
        "ux_label": UX_LABELS[state],
        "batch_confirmable": batch_confirmable,
        "exception_reasons": reasons,
        "critical_visible": critical_visible,
    }


def review_workbench(device_id: str) -> dict[str, Any]:
    devices = {x["id"]: x for x in core.list_devices()}
    if device_id not in devices:
        raise KeyError(device_id)
    device = devices[device_id]
    coverage = _coverage_states(device_id)
    candidates = _candidate_map(device_id)
    fields = parameter_baseline.product_fields(
        device["device_type"], ai.expected_fields(device["device_type"], device.get("vendor", ""))
    )
    rows_out = []
    for field in fields:
        key = field["canonical_name"]
        aliases = field.get("aliases") or [key]
        items = [item for alias in aliases for item in candidates.get(alias, [])]
        coverage_item = next((coverage.get(alias) for alias in aliases if coverage.get(alias)), {}) or {}
        cov_status = _coverage_status(coverage_item)
        base = {
            "canonical_name": key,
            "parameter_name": field.get("parameter_name") or key,
            "group": field.get("group") or parameter_baseline.COMPREHENSIVE,
            "group_label": field.get("group_label") or parameter_baseline.GROUP_LABELS[parameter_baseline.COMPREHENSIVE],
            "requirement_level": field.get("requirement_level") or "FULL",
            "role": field.get("role") or "",
            "coverage_status": cov_status,
            "coverage_reason": coverage_item.get("reason") or "",
        }
        if not items:
            row = {
                **base,
                "candidate_id": None,
                "ai_value": None,
                "ai_unit": "",
                "human_value": None,
                "human_unit": "",
                "condition": "",
                "scope": "",
                "review_status": "NOT_REVIEWED",
                "evidence": [],
                "persistent_evidence": False,
                "verified_by": None,
                "verified_at": None,
                "history": [],
            }
            row.update(_review_ux_state(row))
            rows_out.append(row)
            continue

        for item in items:
            persistent_evidence = list(item.get("evidence") or [])
            evidence = persistent_evidence or [{
                "source_page": item.get("source_page"),
                "source_section": item.get("source_section"),
                "source_text": item.get("source_text"),
                "confidence": item.get("confidence"),
            }]
            try:
                history = core.list_candidate_review_history(item["id"])
            except KeyError:
                history = []
            row = {
                **base,
                "candidate_id": item.get("id"),
                "ai_value": item.get("ai_value"),
                "ai_unit": item.get("ai_unit"),
                "human_value": item.get("final_value"),
                "human_unit": item.get("final_unit"),
                "condition": item.get("condition") or "",
                "scope": item.get("scope") or "",
                "review_status": {"pending": "UNREVIEWED", "confirmed": "CONFIRMED", "rejected": "REJECTED"}.get(item.get("verify_status"), "UNREVIEWED"),
                "evidence": _enrich_evidence(device, evidence),
                "persistent_evidence": bool(persistent_evidence),
                "verified_by": item.get("verified_by"),
                "verified_at": item.get("verified_at"),
                "history": history,
            }
            row.update(_review_ux_state(row))
            rows_out.append(row)

    summary = {state: sum(1 for x in rows_out if x["ux_state"] == state) for state in (
        UX_TRUSTED, UX_NEEDS_ATTENTION, UX_UNKNOWN, UX_NOT_APPLICABLE, UX_CONFIRMED
    )}
    exception_rows = [x for x in rows_out if x["ux_state"] in {UX_NEEDS_ATTENTION, UX_UNKNOWN}]
    critical_rows = [x for x in rows_out if x.get("critical_visible")]
    workflow = core.specification_workflow_status(device_id)
    return {
        "device": device,
        "workflow": workflow,
        "rows": rows_out,
        "ux_summary": {
            "identified": len(rows_out),
            "trusted": summary[UX_TRUSTED],
            "needs_attention": summary[UX_NEEDS_ATTENTION],
            "unknown": summary[UX_UNKNOWN],
            "not_applicable": summary[UX_NOT_APPLICABLE],
            "confirmed": summary[UX_CONFIRMED],
            "exception_count": len(exception_rows),
            "action_required_count": summary[UX_NEEDS_ATTENTION],
            "batch_confirmable_count": sum(1 for x in rows_out if x["batch_confirmable"]),
            "critical_visible_count": len(critical_rows),
        },
        "exception_candidate_ids": [
            x["candidate_id"] for x in rows_out
            if x["ux_state"] == UX_NEEDS_ATTENTION and x.get("candidate_id")
        ],
        "batch_candidate_ids": [x["candidate_id"] for x in rows_out if x["batch_confirmable"]],
        "critical_fields": [
            {"canonical_name": x["canonical_name"], "parameter_name": x["parameter_name"], "ux_state": x["ux_state"]}
            for x in critical_rows
        ],
        "counts": {state: sum(1 for x in rows_out if x["review_status"] == state) for state in ("UNREVIEWED", "CONFIRMED", "REJECTED", "NOT_REVIEWED")},
        "coverage_counts": {state: sum(1 for x in rows_out if x["coverage_status"] == state) for state in ("FOUND", "NOT_FOUND", "NOT_APPLICABLE", "NOT_CHECKED", "AMBIGUOUS")},
    }


def batch_confirm_trusted(device_id: str, verified_by: str) -> dict[str, Any]:
    reviewer = str(verified_by or "").strip()
    if not reviewer:
        raise ValueError("请填写核对人")
    workbench = review_workbench(device_id)
    targets = [x for x in workbench["rows"] if x.get("batch_confirmable")]
    confirmed, failed = [], []
    for row in targets:
        try:
            result = core.verify(
                row["candidate_id"],
                "confirmed",
                row.get("ai_value"),
                row.get("ai_unit"),
                reviewer,
                row.get("condition"),
                row.get("scope"),
                confirm_mode="batch",
            )
            confirmed.append(result)
        except Exception as exc:
            failed.append({"candidate_id": row.get("candidate_id"), "parameter_name": row.get("parameter_name"), "error": str(exc)})
    refreshed = review_workbench(device_id)
    return {
        "device_id": device_id,
        "requested_count": len(targets),
        "confirmed_count": len(confirmed),
        "failed_count": len(failed),
        "confirmed": confirmed,
        "failed": failed,
        "workflow": refreshed["workflow"],
        "ux_summary": refreshed["ux_summary"],
    }


def complete_parameter_review(device_id: str) -> dict[str, Any]:
    workbench = review_workbench(device_id)
    workflow = workbench["workflow"]
    blockers = [
        {
            "canonical_name": x["canonical_name"],
            "parameter_name": x["parameter_name"],
            "ux_state": x["ux_state"],
            "reasons": x.get("exception_reasons") or [],
        }
        for x in workbench["rows"]
        if x["ux_state"] in {UX_NEEDS_ATTENTION, UX_TRUSTED}
        and x.get("requirement_level") in {"MUST", "SHOULD"}
    ]
    non_actionable_missing = [
        {
            "canonical_name": x["canonical_name"],
            "parameter_name": x["parameter_name"],
            "ux_state": x["ux_state"],
            "reasons": x.get("exception_reasons") or [],
        }
        for x in workbench["rows"]
        if x["ux_state"] == UX_UNKNOWN
    ]
    evidence_blockers = [
        str(x) for x in (workflow.get("missing_evidence_fields") or [])
        if str(x).strip()
    ]
    workflow = {
        **workflow,
        "non_actionable_missing": non_actionable_missing,
        "evidence_blockers": evidence_blockers,
    }
    return {
        "device_id": device_id,
        "completed": bool(workflow.get("formal_ready")),
        "workflow": workflow,
        "blockers": blockers,
        "evidence_blockers": evidence_blockers,
        "non_actionable_missing": non_actionable_missing,
        "gate": "CONFIRMED_DEVICE_FACT_GATE_UNCHANGED",
    }


def dashboard() -> dict[str, Any]:
    devices = core.list_devices()
    summaries = []
    for d in devices:
        detail = device_slots(d["id"])
        attention = detail["counts"]["NOT_CHECKED"] + detail["counts"]["AMBIGUOUS"] + detail["counts"]["UNREVIEWED"]
        summaries.append({
            "id": d["id"],
            "vendor": d["vendor"],
            "model": d["model"],
            "device_type": d["device_type"],
            "coverage_ratio": detail["coverage_ratio"],
            "search_coverage_ratio": detail["search_coverage_ratio"],
            "fact_coverage_ratio": detail["fact_coverage_ratio"],
            "attention": attention,
            "counts": detail["counts"],
            "lifecycle": detail["lifecycle"],
        })
    by_type = {}
    for d in devices:
        by_type[d["device_type"]] = by_type.get(d["device_type"], 0) + 1
    formal = [x for x in summaries if x["lifecycle"]["formal_ready"]]
    return {
        "device_count": len(devices),
        "formal_device_count": len(formal),
        "draft_device_count": len(devices) - len(formal),
        "confirmed_fact_count": sum(int(d.get("confirmed_candidate_count") or 0) for d in devices),
        "by_type": by_type,
        "attention_count": sum(x["attention"] for x in summaries),
        "devices": summaries,
        "recent_assessments": core.list_recent_device_assessments(limit=10),
        "knowledge_release": KnowledgeReleaseConsumer.current().status(),
    }

_COMPARE_SIZE_FACTORS = {
    "B": Decimal(1),
    "byte": Decimal(1),
    "bytes": Decimal(1),
    "KB": Decimal(1000),
    "MB": Decimal(1000) ** 2,
    "GB": Decimal(1000) ** 3,
    "TB": Decimal(1000) ** 4,
    "PB": Decimal(1000) ** 5,
    "KiB": Decimal(1024),
    "MiB": Decimal(1024) ** 2,
    "GiB": Decimal(1024) ** 3,
    "TiB": Decimal(1024) ** 4,
    "Kb": Decimal(1000) / Decimal(8),
    "Mb": (Decimal(1000) ** 2) / Decimal(8),
    "Gb": (Decimal(1000) ** 3) / Decimal(8),
    "Tb": (Decimal(1000) ** 4) / Decimal(8),
}
_COMPARE_TIME_FACTORS = {
    "ns": Decimal("0.000000001"),
    "us": Decimal("0.000001"),
    "µs": Decimal("0.000001"),
    "μs": Decimal("0.000001"),
    "ms": Decimal("0.001"),
    "s": Decimal(1),
}


def _compare_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _comparison_fact_key(canonical_name: str, cell: dict[str, Any]) -> tuple[Any, ...]:
    """Compare formal facts by engineering-equivalent value plus condition/scope.

    Only explicit unit families are normalized. Unknown units stay literal so
    the comparison never guesses a conversion.
    """
    value = cell.get("value")
    unit = str(cell.get("unit") or "").strip()
    number = _compare_decimal(value)
    normalized_value: Any = str(value or "").strip()
    normalized_unit = unit

    if number is not None and canonical_name in {
        "capacity", "tbw", "page_size", "block_size", "erase_granularity",
    } and unit in _COMPARE_SIZE_FACTORS:
        normalized_value = number * _COMPARE_SIZE_FACTORS[unit]
        normalized_unit = "bytes"
    elif number is not None and canonical_name in {"program_time", "erase_time"} and unit in _COMPARE_TIME_FACTORS:
        normalized_value = number * _COMPARE_TIME_FACTORS[unit]
        normalized_unit = "seconds"
    elif number is not None and canonical_name in {"pe_cycles", "pages_per_block", "dwpd"}:
        normalized_value = number
        normalized_unit = "" if unit.lower() in {"", "cycle", "cycles", "x"} else unit

    if isinstance(normalized_value, Decimal):
        normalized_value = normalized_value.normalize()

    condition = " ".join(str(cell.get("condition") or "").split())
    scope = " ".join(str(cell.get("scope") or "").split())
    return (
        normalized_value,
        normalized_unit,
        condition,
        scope,
        str(cell.get("status") or ""),
    )


def compare_devices(device_ids: list[str]) -> dict[str, Any]:
    ids = [str(x or "").strip() for x in device_ids if str(x or "").strip()]
    if len(ids) < 2 or len(ids) > 4:
        raise ValueError("器件对比仅支持 2～4 个器件")
    if len(set(ids)) != len(ids):
        raise ValueError("器件对比不能包含重复器件")
    details = [device_slots(x) for x in ids]
    normalized_types = {
        templates.normalize_device_type(x["device"]["device_type"])
        for x in details
    }
    comparable_types = len(normalized_types) == 1
    field_order = []
    by_device = {}
    for detail in details:
        did = detail["device"]["id"]
        by_device[did] = {x["canonical_name"]: x for x in detail["slots"]}
        for x in detail["slots"]:
            if x["canonical_name"] not in field_order:
                field_order.append(x["canonical_name"])
    rows = []
    for key in field_order:
        raw_cells = {did: by_device[did].get(key, {"canonical_name": key, "parameter_name": key, "status": "NOT_APPLICABLE", "coverage_status": "NOT_APPLICABLE", "review_status": "NOT_REVIEWED", "value": None, "unit": "", "evidence": []}) for did in by_device}
        cells = {}
        for did, cell in raw_cells.items():
            formal = cell.get("review_status") == "CONFIRMED"
            cells[did] = {
                "canonical_name": cell.get("canonical_name"), "parameter_name": cell.get("parameter_name"),
                "status": cell.get("status"), "coverage_status": cell.get("coverage_status"), "review_status": cell.get("review_status"),
                "value": cell.get("value") if formal else None, "unit": cell.get("unit") if formal else "",
                "condition": cell.get("condition") if formal else "", "scope": cell.get("scope") if formal else "",
                "evidence": cell.get("evidence") if formal else [],
            }
        values = {_comparison_fact_key(key, c) for c in cells.values()}
        missing = any(
            c.get("review_status") != "CONFIRMED"
            and c.get("status") != "NOT_APPLICABLE"
            for c in cells.values()
        )
        exemplar = next((x for x in details[0]["slots"] if x.get("canonical_name") == key), {})
        parameter_name = next((c.get("parameter_name") for c in cells.values() if c.get("parameter_name")), key)
        knowledge = (
            _formal_knowledge(
                key,
                parameter_name,
                details[0]["device"]["device_type"],
                context="comparison difference engineering meaning",
            )
            if comparable_types
            else {
                "status": "NOT_APPLICABLE",
                "code": "DEVICE_TYPE_MISMATCH_RAW_FACT_COMPARE_ONLY",
                "knowledge_release_version": None,
                "results": [],
                "evidence_refs": [],
            }
        )
        rows.append({"canonical_name": key, "parameter_name": parameter_name, "group": exemplar.get("group") or parameter_baseline.COMPREHENSIVE, "group_label": exemplar.get("group_label") or parameter_baseline.GROUP_LABELS[parameter_baseline.COMPREHENSIVE], "cells": cells, "is_difference": len(values) > 1, "has_missing": missing, "formal_knowledge": knowledge})
    return {
        "devices": [x["device"] for x in details],
        "rows": rows,
        "comparison_scope": "ENGINEERING_COMPARABLE" if comparable_types else "RAW_FACT_ONLY",
        "device_types": sorted(normalized_types),
    }


RUNTIME_TEXT_PATTERNS = [
    ("percentage_used", r"\bpercentage[ _-]*used\b\s*[:=]\s*([^\s]+)", "%", "NVME_PERCENTAGE_USED_INTERPRETATION_V1"),
    ("data_units_written", r"\bdata[ _-]*units[ _-]*written\b\s*[:=]\s*([^\s]+)", "data_units", "NVME_DATA_UNITS_WRITTEN_V1"),
    ("critical_warning", r"\bcritical[ _-]*warning\b\s*[:=]\s*([^\s]+)", "code", None),
    ("media_errors", r"\bmedia[ _-]*(?:and[ _-]*data[ _-]*integrity[ _-]*)?errors?\b\s*[:=]\s*([^\s]+)", "count", None),
    ("available_spare", r"\bavailable[ _-]*spare\b\s*[:=]\s*([^\s]+)", "%", None),
    ("available_spare_threshold", r"\bavailable[ _-]*spare[ _-]*threshold\b\s*[:=]\s*([^\s]+)", "%", None),
    ("device_life_time_est_typ_a", r"\b(?:device[ _-]*life[ _-]*time[ _-]*(?:estimation|estimate|est)?[ _-]*(?:type|typ)[ _-]*a|device_life_time_est_typ_a|ext_csd_device_life_time_est_typ_a)\b[^\n:]{0,80}[:=]\s*([^\s\]]+)", "code", "EMMC_DEVICE_LIFE_TIME_A_V1"),
    ("device_life_time_est_typ_b", r"\b(?:device[ _-]*life[ _-]*time[ _-]*(?:estimation|estimate|est)?[ _-]*(?:type|typ)[ _-]*b|device_life_time_est_typ_b|ext_csd_device_life_time_est_typ_b)\b[^\n:]{0,80}[:=]\s*([^\s\]]+)", "code", "EMMC_DEVICE_LIFE_TIME_B_V1"),
    ("pre_eol_info", r"\b(?:pre[ _-]*eol(?:[ _-]*(?:info|information))?|ext_csd_pre_eol_info)\b[^\n:]{0,80}[:=]\s*([^\s\]]+)", "code", "EMMC_PRE_EOL_V1"),
    ("runtime_bad_block", r"\b(?:runtime[ _-]*)?bad[ _-]*blocks?\b\s*[:=]\s*([^\s]+)", "count", None),
    ("program_fail", r"\bprogram[ _-]*fails?\b\s*[:=]\s*([^\s]+)", "count", None),
    ("erase_fail", r"\berase[ _-]*fails?\b\s*[:=]\s*([^\s]+)", "count", None),
    ("erase_count", r"\berase[ _-]*count\b\s*[:=]\s*([^\s]+)", "cycles", "NAND_ERASE_COUNT_MARGIN_V1"),
    ("pe_cycle", r"\b(?:p[ _-]*/?[ _-]*e|pe)[ _-]*(?:cycle|count)s?\b\s*[:=]\s*([^\s]+)", "cycles", "NAND_PE_MARGIN_V1"),
    ("ecc_corrected", r"\becc[ _-]*corrected\b\s*[:=]\s*([^\s]+)", "count", None),
    ("ecc_uncorrectable", r"\becc[ _-]*(?:uncorrectable|uncorrected)\b\s*[:=]\s*([^\s]+)", "count", None),
    ("bit_flip_count", r"\b(?:bit[ _-]*flips?|bitflip(?:[ _-]*count)?|corrected[ _-]*bits?)\b\s*[:=]\s*([^\s]+)", "count", None),
    ("bit_flip_threshold", r"\b(?:bit[ _-]*flip|bitflip)[ _-]*(?:threshold|limit)\b\s*[:=]\s*([^\s]+)", "count", None),
]


def _runtime_text_value(raw: str) -> Any:
    value = str(raw or "").strip().rstrip(",;")
    clean = value.replace(",", "").rstrip("%")
    try:
        if clean.lower().startswith("0x"):
            return int(clean, 16)
        if "." in clean:
            return float(clean)
        return int(clean)
    except ValueError:
        return value


def parse_runtime_observation_text(
    device_type: str,
    text: str,
    source_label: str = "PASTED_RUNTIME_OUTPUT",
) -> dict[str, Any]:
    """Parse common runtime-health text without making a diagnosis.

    The parser only extracts explicit named values from pasted tool output.
    It does not infer missing metrics or convert vendor-specific semantics.
    """
    raw_text = str(text or "")
    if not raw_text.strip():
        raise ValueError("RUNTIME_TEXT_REQUIRED")
    if len(raw_text) > 200_000:
        raise ValueError("RUNTIME_TEXT_TOO_LARGE")

    observations = []
    seen = set()
    for metric_name, pattern, unit, lifetime_metric in RUNTIME_TEXT_PATTERNS:
        match = re.search(pattern, raw_text, flags=re.IGNORECASE | re.MULTILINE)
        if not match:
            continue
        raw_value = match.group(1).strip()
        key = (metric_name, raw_value)
        if key in seen:
            continue
        seen.add(key)
        line_start = raw_text.rfind("\n", 0, match.start()) + 1
        line_end = raw_text.find("\n", match.end())
        if line_end < 0:
            line_end = len(raw_text)
        source_line = raw_text[line_start:line_end].strip()
        observations.append({
            "metric_name": metric_name,
            "raw_value": raw_value,
            "normalized_value": _runtime_text_value(raw_value),
            "unit": unit,
            "source_line": source_line,
            "source_label": source_label,
            "diagnostic_supported": (
                metric_name in DIAGNOSTIC_METHODS
                or metric_name.startswith("ecc_")
                or metric_name == "available_spare_threshold"
            ),
            "suggested_lifetime_metric": lifetime_metric,
        })

    return {
        "device_type": templates.normalize_device_type(device_type) if device_type else "",
        "source_label": source_label,
        "observation_count": len(observations),
        "observations": observations,
        "parser_scope": "EXPLICIT_NAMED_RUNTIME_VALUES_ONLY",
        "diagnosis_performed": False,
        "public_knowledge_used": False,
    }


def diagnostics(device_type: str = "", device_id: str = "") -> dict[str, Any]:
    dtype = templates.normalize_device_type(device_type) if device_type else ""
    evidence_by_field = {}
    lifecycle = None
    detail = None
    if device_id:
        detail = device_slots(device_id)
        lifecycle = detail["lifecycle"]
        dtype = templates.normalize_device_type(detail["device"]["device_type"])
        evidence_by_field = {x["canonical_name"]: x for x in detail["slots"]}
    if not dtype:
        dtype = "eMMC"
    rows = []
    for field in ai.expected_fields(dtype):
        key = field["canonical_name"]
        if field.get("role") != "diagnostic" and key not in DIAGNOSTIC_METHODS:
            continue
        data_source, method, interpretation = DIAGNOSTIC_METHODS.get(key, ("Datasheet / 运行接口", "按器件/控制器定义读取", "结合趋势、阈值和业务负载人工判读"))
        slot = evidence_by_field.get(key) or {}
        formal = slot.get("review_status") == "CONFIRMED"
        knowledge = slot.get("formal_knowledge") or _formal_knowledge(
            key,
            field.get("parameter_name") or key,
            dtype,
            context="diagnostic read method interpretation lifetime health",
        )
        rows.append({
            "canonical_name": key,
            "indicator": field.get("parameter_name") or key,
            "engineering_meaning": field.get("note") or "",
            "data_source": data_source,
            "read_method": method,
            "interpretation": interpretation,
            "fact_status": slot.get("status", "NOT_CHECKED") if device_id else "REFERENCE",
            "review_status": slot.get("review_status", "NOT_REVIEWED") if device_id else "REFERENCE",
            "diagnostic_status": slot.get("diagnostic_status") if device_id else None,
            "diagnostic_label": slot.get("diagnostic_label") if device_id else None,
            "evidence": slot.get("evidence", []) if formal else [],
            "runtime_observation": {
                "status": "UNKNOWN",
                "code": "RUNTIME_OBSERVATION_UNAVAILABLE",
                "observed_at": None,
                "value": None,
                "source": None,
            },
            "formal_knowledge": knowledge,
            "guidance_source": "FORMAL_KNOWLEDGE" if knowledge["status"] == "MATCHED" else "KNOWLEDGE_GAP",
        })

    skill_result = None
    if detail is not None:
        # Reuse the existing Storage Domain Skill adapter.  Product API composes context;
        # it does not implement a second diagnostic skill or protocol knowledge stack.
        from skills.real_knowledge import RealKnowledgeAssessmentService
        capabilities = [
            {
                "canonical_name": x["canonical_name"],
                "diagnostic_status": x.get("diagnostic_status"),
                "datasheet_fact": x.get("value"),
                "review_status": x.get("review_status"),
                "evidence_refs": [
                    e.get("evidence_id") or e.get("source_id")
                    for e in x.get("evidence") or []
                    if e.get("evidence_id") or e.get("source_id")
                ],
            }
            for x in detail["slots"]
            if x.get("group") == parameter_baseline.KEY_DIAGNOSTIC
        ]
        skill_result = RealKnowledgeAssessmentService.current().execute_skill(
            "storage-diagnostic-validation",
            {
                "device_type": dtype,
                "target_question": f"{dtype} diagnostic capability validation and runtime observation requirements",
                "diagnostic_capabilities": capabilities,
                "runtime_observations": [],
            },
        )
    knowledge_gap = any(x.get("diagnostic_status") == DIAG_KNOWLEDGE_GAP for x in rows)
    if skill_result and skill_result.get("status") == "INSUFFICIENT_KNOWLEDGE":
        knowledge_gap = True
    return {
        "device_type": dtype,
        "device_id": device_id or None,
        "items": rows,
        # Keep the RC1 response contract stable for existing consumers.
        "layers": ["DATASHEET_FACT", "RUNTIME_OBSERVATION", "KNOWLEDGE"],
        # #359 makes the product semantics explicit without mutating the frozen key.
        "semantic_layers": ["DATASHEET_FACT", "DOMAIN_KNOWLEDGE", "RUNTIME_OBSERVATION"],
        "result_status": "PARTIAL" if knowledge_gap else "READY",
        "knowledge_gap": knowledge_gap,
        "lifecycle_gate": lifecycle,
        "formal_consumption_allowed": bool(lifecycle and lifecycle.get("formal_ready")) if device_id else None,
        "skill_id": "storage-diagnostic-validation",
        "skill_result": skill_result,
        "engineering_result": _engineering_result_view(skill_result) if skill_result else None,
    }


def change_impact(old_id: str, new_id: str) -> dict[str, Any]:
    comparison = compare_devices([old_id, new_id])
    old_type = templates.normalize_device_type(comparison["devices"][0]["device_type"])
    new_type = templates.normalize_device_type(comparison["devices"][1]["device_type"])
    if old_type != new_type:
        raise ValueError(
            f"DEVICE_TYPE_MISMATCH_FOR_CHANGE_IMPACT:{old_type}->{new_type};"
            " raw parameter compare remains available, but engineering impact requires comparable device types"
        )
    rows = []
    unknowns = []
    for row in comparison["rows"]:
        a = row["cells"].get(old_id) or {}
        b = row["cells"].get(new_id) or {}
        if not row["is_difference"] and not row["has_missing"]:
            continue
        statuses = {str(a.get("status") or ""), str(b.get("status") or "")}
        if any(status not in {"CONFIRMED", "NOT_APPLICABLE"} for status in statuses):
            confidence = "LOW"
            unknowns.append(f"{row['parameter_name']} 存在未确认/缺失/已驳回事实，必须先验证")
        else:
            confidence = "EVIDENCED"
        meaning, sw, test, monitor = IMPACT_RULES.get(row["canonical_name"], ("参数能力发生变化或存在事实缺口", "复核相关软件配置、异常处理和持久化策略", "补充该参数相关边界与回归测试", "复核对应运行监控/告警"))
        knowledge = _formal_knowledge(
            row["canonical_name"],
            row["parameter_name"],
            comparison["devices"][0]["device_type"],
            context="change impact software test monitoring lifetime risk",
        )
        if knowledge["status"] == "MATCHED":
            first = knowledge["results"][0]
            meaning = str(first.get("summary") or first.get("content") or meaning)
        rows.append({
            "canonical_name": row["canonical_name"],
            "parameter_name": row["parameter_name"],
            "old": a,
            "new": b,
            "confidence": confidence,
            "technical_meaning": meaning,
            "software_impact": sw,
            "test_impact": test,
            "monitoring_impact": monitor,
            "validation_item": "确认事实差异、适用条件和 Evidence 后由工程人员完成最终判定",
            "formal_knowledge": knowledge,
            "knowledge_analysis_source": "FORMAL_KNOWLEDGE" if knowledge["status"] == "MATCHED" else "STATIC_FALLBACK",
        })
    from skills.real_knowledge import RealKnowledgeAssessmentService
    skill_delta = [
        {
            "canonical_name": row["canonical_name"],
            "old": row["old"].get("value"),
            "new": row["new"].get("value"),
            "old_status": row["old"].get("status"),
            "new_status": row["new"].get("status"),
            "evidence_refs": [
                e.get("evidence_id") or e.get("source_id")
                for cell in (row["old"], row["new"])
                for e in cell.get("evidence") or []
                if e.get("evidence_id") or e.get("source_id")
            ],
        }
        for row in rows
    ]
    skill_result = RealKnowledgeAssessmentService.current().execute_skill(
        "storage-change-impact",
        {
            "device_type": comparison["devices"][0]["device_type"],
            "parameter_delta": skill_delta,
            "question": "device parameter change lifetime software monitoring validation impact",
        },
    )
    return {
        "old_id": old_id,
        "new_id": new_id,
        "status": "DRAFT_FOR_ENGINEERING_REVIEW",
        "final_replacement_decision": None,
        "items": rows,
        "unknowns": list(dict.fromkeys(unknowns)),
        "skill_id": "storage-change-impact",
        "skill_result": skill_result,
        "engineering_result": _engineering_result_view(skill_result),
    }


def record_change_impact(old_id: str, new_id: str, *, assessment_author: str = "Storage MVP UI") -> dict[str, Any]:
    result = change_impact(old_id, new_id)
    skill = result.get("skill_result") or {}
    old_detail = device_slots(old_id)
    new_detail = device_slots(new_id)
    rows = list(result.get("items") or [])
    unknowns = list(result.get("unknowns") or [])
    facts_evidenced = all(
        str(x.get("confidence") or "").upper() == "EVIDENCED"
        for x in rows
    )
    # S2 may legitimately finish from deterministic Storage impact rules even
    # when the current Formal Knowledge Release has no matching object.  This
    # is still a DRAFT_FOR_ENGINEERING_REVIEW and never an auto replacement decision.
    product_status = "ANSWERED" if facts_evidenced and not unknowns else (
        skill.get("status") or result.get("status") or "UNKNOWN"
    )
    result["product_assessment_status"] = product_status
    old_fingerprint = _device_fact_fingerprint(old_detail)
    new_fingerprint = _device_fact_fingerprint(new_detail)
    knowledge_identity = _knowledge_release_identity()
    record_input = {
        "old_id": old_id,
        "new_id": new_id,
        "_old_device_fact_fingerprint": old_fingerprint,
        "_new_device_fact_fingerprint": new_fingerprint,
        "_knowledge_release_identity": knowledge_identity,
    }

    # Compare is auto-triggered from the product flow.  Reopening the same
    # unchanged A->B comparison must not create duplicate assessment history.
    existing = next(
        (
            item for item in core.list_device_assessments(new_id, limit=50)
            if str(item.get("assessment_type") or "").upper() == "COMPARE"
            and str((item.get("input") or {}).get("old_id") or "") == old_id
            and str((item.get("input") or {}).get("new_id") or "") == new_id
            and str((item.get("input") or {}).get("_old_device_fact_fingerprint") or "") == old_fingerprint
            and str((item.get("input") or {}).get("_new_device_fact_fingerprint") or "") == new_fingerprint
            and dict((item.get("input") or {}).get("_knowledge_release_identity") or {}) == knowledge_identity
            and str(item.get("status") or "").upper() == str(product_status or "").upper()
        ),
        None,
    )
    if existing is not None:
        result["assessment_record"] = existing
        result["assessment_reused"] = True
        return result

    record = core.save_device_assessment(
        new_id,
        "COMPARE",
        product_status,
        record_input,
        result,
        created_by=assessment_author,
    )
    result["assessment_record"] = record
    result["assessment_reused"] = False
    return result


def maintenance() -> dict[str, Any]:
    devices = core.list_devices()
    import_review = []
    for d in devices:
        detail = device_slots(d["id"])
        review = review_workbench(d["id"])
        ux = review["ux_summary"]
        if ux["trusted"] or ux["needs_attention"] or ux["unknown"]:
            import_review.append({
                "device_id": d["id"],
                "model": d["model"],
                "device_type": d["device_type"],
                "counts": detail["counts"],
                "ux_summary": ux,
            })
    from . import knowledge as knowledge_store
    sources = knowledge_store.list_sources()
    return {
        "datasheet_review_queue": import_review,
        "knowledge_source_queue": [x for x in sources if x.get("verify_status") != "verified"],
        "knowledge_release": KnowledgeReleaseConsumer.current().status(),
        "publish_gate": {
            "owner": "Knowledge Production",
            "storage_can_publish": False,
            "rule": "Storage only consumes formally published Knowledge Release; AI/source review cannot auto-publish formal knowledge.",
        },
    }
