from __future__ import annotations

from typing import Any

from . import ai, core, templates, parameter_baseline
from .knowledge_release import KnowledgeReleaseConsumer

UI_STATUS = {
    "FOUND": "FOUND",
    "NOT_SPECIFIED": "NOT_FOUND",
    "NOT_APPLICABLE": "NOT_APPLICABLE",
    "UNRESOLVED": "NOT_CHECKED",
}

DIAGNOSTIC_METHODS = {
    "life_time_a": ("EXT_CSD", "读取 DEVICE_LIFE_TIME_EST_TYP_A", "按 JEDEC/厂商定义解释寿命区间"),
    "life_time_b": ("EXT_CSD", "读取 DEVICE_LIFE_TIME_EST_TYP_B", "结合对应存储区域解释寿命区间"),
    "pre_eol": ("EXT_CSD", "读取 PRE_EOL_INFO", "按正常/预警/紧急状态解释预留块风险"),
    "ext_csd_health_report": ("EXT_CSD", "读取设备健康字段", "与 Life Time A/B、PRE_EOL 联合判断"),
    "bkops": ("EXT_CSD", "读取 BKOPS_STATUS / BKOPS_EN", "判断后台维护需求与长期运行风险"),
    "percentage_used": ("NVMe SMART / Health", "读取 Percentage Used", "作为耐久消耗估计，不能单独替代厂商寿命模型"),
    "data_units_written": ("NVMe SMART / Health", "读取 Data Units Written 并换算主机写入量", "与 TBW/工作负载联合评估"),
    "smart_health": ("SMART / NVMe Health", "读取健康日志", "结合 Critical Warning、Media Errors、温度等联合判断"),
    "media_errors": ("SMART / NVMe Health", "读取介质错误计数", "持续增长需结合日志与业务负载分析"),
    "critical_warning": ("NVMe SMART / Health", "读取 Critical Warning 位", "任一关键告警均进入人工诊断"),
    "available_spare": ("NVMe SMART / Health", "读取 Available Spare / Threshold", "低于阈值进入寿命风险关注"),
    "ecc_status": ("器件状态寄存器/驱动统计", "读取 ECC corrected/uncorrectable 状态", "观察纠错压力与不可纠正错误趋势"),
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
    fields = parameter_baseline.product_fields(device["device_type"], ai.expected_fields(device["device_type"]))
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
            "formal_knowledge": (
                _formal_knowledge(
                    key,
                    field.get("parameter_name") or key,
                    device["device_type"],
                    context="engineering meaning diagnostic lifetime",
                )
                if review_status == "CONFIRMED"
                else {
                    "status": "NOT_APPLICABLE",
                    "code": "DEVICE_FACT_NOT_CONFIRMED",
                    "knowledge_release_version": None,
                    "results": [],
                    "evidence_refs": [],
                }
            ),
        })
    states = ("CONFIRMED", "UNREVIEWED", "REJECTED", "NOT_FOUND", "NOT_CHECKED", "AMBIGUOUS", "NOT_APPLICABLE")
    counts = {state: sum(1 for x in slots if x["status"] == state) for state in states}
    coverage_known = sum(1 for x in slots if x["coverage_status"] in {"FOUND", "NOT_FOUND", "NOT_APPLICABLE"})
    facts = [
        {k: slot.get(k) for k in ("canonical_name", "parameter_name", "value", "unit", "condition", "scope", "evidence", "verified_by", "verified_at")}
        for slot in slots if slot["review_status"] == "CONFIRMED"
    ]
    return {
        "device": device,
        "slots": slots,
        "device_facts": facts,
        "counts": counts,
        "coverage_ratio": round(coverage_known / len(slots), 4) if slots else 0,
        "workflow": core.specification_workflow_status(device_id),
        "lifecycle": _device_lifecycle(device_id),
        "conclusion": core.get_device_conclusion(device_id),
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


def review_workbench(device_id: str) -> dict[str, Any]:
    devices = {x["id"]: x for x in core.list_devices()}
    if device_id not in devices:
        raise KeyError(device_id)
    device = devices[device_id]
    coverage = _coverage_states(device_id)
    candidates = _candidate_map(device_id)
    fields = parameter_baseline.product_fields(device["device_type"], ai.expected_fields(device["device_type"]))
    rows_out = []
    for field in fields:
        key = field["canonical_name"]
        aliases = field.get("aliases") or [key]
        items = [item for alias in aliases for item in candidates.get(alias, [])]
        coverage_item = next((coverage.get(alias) for alias in aliases if coverage.get(alias)), {}) or {}
        cov_status = _coverage_status(coverage_item)
        if not items:
            rows_out.append({
                "canonical_name": key, "parameter_name": field.get("parameter_name") or key,
                "group": field.get("group") or parameter_baseline.COMPREHENSIVE,
                "group_label": field.get("group_label") or parameter_baseline.GROUP_LABELS[parameter_baseline.COMPREHENSIVE],
                "requirement_level": field.get("requirement_level") or "FULL",
                "candidate_id": None, "ai_value": None, "ai_unit": "", "human_value": None, "human_unit": "",
                "condition": "", "scope": "", "coverage_status": cov_status, "coverage_reason": coverage_item.get("reason") or "",
                "review_status": "NOT_REVIEWED", "evidence": [], "verified_by": None, "verified_at": None, "history": [],
            })
            continue
        for item in items:
            evidence = item.get("evidence") or [{
                "source_page": item.get("source_page"), "source_section": item.get("source_section"),
                "source_text": item.get("source_text"), "confidence": item.get("confidence"),
            }]
            try:
                history = core.list_candidate_review_history(item["id"])
            except KeyError:
                history = []
            rows_out.append({
                "canonical_name": key, "parameter_name": field.get("parameter_name") or key,
                "group": field.get("group") or parameter_baseline.COMPREHENSIVE,
                "group_label": field.get("group_label") or parameter_baseline.GROUP_LABELS[parameter_baseline.COMPREHENSIVE],
                "requirement_level": field.get("requirement_level") or "FULL",
                "candidate_id": item.get("id"), "ai_value": item.get("ai_value"), "ai_unit": item.get("ai_unit"),
                "human_value": item.get("final_value"), "human_unit": item.get("final_unit"),
                "condition": item.get("condition") or "", "scope": item.get("scope") or "",
                "coverage_status": cov_status, "coverage_reason": coverage_item.get("reason") or "",
                "review_status": {"pending": "UNREVIEWED", "confirmed": "CONFIRMED", "rejected": "REJECTED"}.get(item.get("verify_status"), "UNREVIEWED"),
                "evidence": _enrich_evidence(device, evidence), "verified_by": item.get("verified_by"),
                "verified_at": item.get("verified_at"), "history": history,
            })
    return {
        "device": device,
        "workflow": core.specification_workflow_status(device_id),
        "rows": rows_out,
        "counts": {state: sum(1 for x in rows_out if x["review_status"] == state) for state in ("UNREVIEWED", "CONFIRMED", "REJECTED", "NOT_REVIEWED")},
        "coverage_counts": {state: sum(1 for x in rows_out if x["coverage_status"] == state) for state in ("FOUND", "NOT_FOUND", "NOT_APPLICABLE", "NOT_CHECKED", "AMBIGUOUS")},
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
        "knowledge_release": KnowledgeReleaseConsumer.current().status(),
    }

def compare_devices(device_ids: list[str]) -> dict[str, Any]:
    if len(set(device_ids)) < 2:
        raise ValueError("至少选择两个不同器件")
    details = [device_slots(x) for x in device_ids]
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
        values = {(str(c.get("value") or ""), str(c.get("unit") or ""), c.get("status")) for c in cells.values()}
        missing = any(c.get("review_status") != "CONFIRMED" for c in cells.values())
        exemplar = next((x for x in details[0]["slots"] if x.get("canonical_name") == key), {})
        parameter_name = next((c.get("parameter_name") for c in cells.values() if c.get("parameter_name")), key)
        knowledge = _formal_knowledge(
            key,
            parameter_name,
            details[0]["device"]["device_type"],
            context="comparison difference engineering meaning",
        )
        rows.append({"canonical_name": key, "parameter_name": parameter_name, "group": exemplar.get("group") or parameter_baseline.COMPREHENSIVE, "group_label": exemplar.get("group_label") or parameter_baseline.GROUP_LABELS[parameter_baseline.COMPREHENSIVE], "cells": cells, "is_difference": len(values) > 1, "has_missing": missing, "formal_knowledge": knowledge})
    return {"devices": [x["device"] for x in details], "rows": rows}


def diagnostics(device_type: str = "", device_id: str = "") -> dict[str, Any]:
    dtype = templates.normalize_device_type(device_type) if device_type else ""
    evidence_by_field = {}
    if device_id:
        _require_formal_device(device_id)
        detail = device_slots(device_id)
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
        knowledge = _formal_knowledge(
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
            "evidence": slot.get("evidence", []) if formal else [],
            "runtime_observation": {
                "status": "UNKNOWN",
                "code": "RUNTIME_OBSERVATION_UNAVAILABLE",
                "observed_at": None,
                "value": None,
                "source": None,
            },
            "formal_knowledge": knowledge,
            "guidance_source": "FORMAL_KNOWLEDGE" if knowledge["status"] == "MATCHED" else "STATIC_FALLBACK",
        })
    return {"device_type": dtype, "device_id": device_id or None, "items": rows, "layers": ["DATASHEET_FACT", "RUNTIME_OBSERVATION", "KNOWLEDGE"]}


def change_impact(old_id: str, new_id: str) -> dict[str, Any]:
    comparison = compare_devices([old_id, new_id])
    rows = []
    unknowns = []
    for row in comparison["rows"]:
        a = row["cells"].get(old_id) or {}
        b = row["cells"].get(new_id) or {}
        if not row["is_difference"] and not row["has_missing"]:
            continue
        statuses = {a.get("status"), b.get("status")}
        if statuses & {"NOT_FOUND", "NOT_CHECKED", "AMBIGUOUS", "UNREVIEWED"}:
            confidence = "LOW"
            unknowns.append(f"{row['parameter_name']} 存在未确认/缺失事实，必须先验证")
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
    return {"old_id": old_id, "new_id": new_id, "status": "DRAFT_FOR_ENGINEERING_REVIEW", "final_replacement_decision": None, "items": rows, "unknowns": list(dict.fromkeys(unknowns))}


def maintenance() -> dict[str, Any]:
    devices = core.list_devices()
    import_review = []
    for d in devices:
        detail = device_slots(d["id"])
        if detail["counts"]["UNREVIEWED"] or detail["counts"]["AMBIGUOUS"] or detail["counts"]["NOT_CHECKED"]:
            import_review.append({"device_id": d["id"], "model": d["model"], "device_type": d["device_type"], "counts": detail["counts"]})
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
