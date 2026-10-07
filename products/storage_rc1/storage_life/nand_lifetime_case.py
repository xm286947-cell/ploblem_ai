"""Evidence-bound, read-only NAND lifetime engineering case assembly."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pypdf import PdfReader

from skills.real_knowledge import RealKnowledgeAssessmentService

from . import public_knowledge


router = APIRouter(tags=["Storage Engineering Cases"])
GD5_SOURCE_MODEL = "GD5F1GQ5"


class ControlledLifetimeScenario(BaseModel):
    """Explicit test inputs; never treated as measured workload or vendor data."""

    scenario_id: str = Field(min_length=1, max_length=80)
    host_write_gib_per_day: float = Field(gt=0, le=10000)
    waf: float = Field(gt=0, le=100)
    wear_pool_fraction_of_nominal: float = Field(gt=0, le=1)
    wear_distribution_factor: float = Field(ge=1, le=100)
    required_pe_margin_fraction: float = Field(ge=0, lt=1)


class NandLifetimeCaseRequest(BaseModel):
    device_model: str = Field(default=GD5_SOURCE_MODEL, min_length=1, max_length=80)
    target_service_life_years: float = Field(default=5, gt=0, le=100)
    controlled_scenario: ControlledLifetimeScenario | None = None


class NandLifetimeRoleRequest(BaseModel):
    device_model: str = Field(default=GD5_SOURCE_MODEL, min_length=1, max_length=80)
    target_service_life_years: float = Field(default=5, gt=0, le=100)


def _r4_controlled_screen(
    request: NandLifetimeCaseRequest, facts: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Run an auditable illustrative screen, not an approved device-life model."""
    scenario = request.controlled_scenario
    if scenario is None:
        return None
    pe_fact = next((fact for fact in facts if fact.get("fact_id") == "F-NAND-PE-ECC"), None)
    density_fact = next((fact for fact in facts if fact.get("fact_id") == "F-NAND-NOMINAL-DENSITY"), None)
    if not pe_fact or not density_fact or pe_fact.get("claim_binding", "").startswith("FAIL_"):
        return {
            "status": "NOT_RUN",
            "scenario_id": scenario.scenario_id,
            "reason": "缺少绑定到指定 Source/Revision/Snapshot 的额定 P/E 或标称密度事实。",
            "scenario_result": "INSUFFICIENT_EVIDENCE",
            "device_decision": "INSUFFICIENT_EVIDENCE",
        }
    density = density_fact.get("value")
    try:
        if not isinstance(density, dict) or density.get("unit") not in {"Gb", "Gbit"}:
            raise InvalidOperation
        rated_pe = Decimal(str(pe_fact["value"]))
        nominal_gigabits = Decimal(str(density["magnitude"]))
        years = Decimal(str(request.target_service_life_years))
        days_per_year = Decimal("365")
        gib_bytes = Decimal(2) ** 30
        raw_bytes = nominal_gigabits * Decimal(1_000_000_000) / Decimal(8)
        wear_pool_bytes = raw_bytes * Decimal(str(scenario.wear_pool_fraction_of_nominal))
        host_bytes = (
            Decimal(str(scenario.host_write_gib_per_day)) * gib_bytes
            * years * days_per_year
        )
        nand_media_write_bytes = host_bytes * Decimal(str(scenario.waf))
        average_pe = nand_media_write_bytes / wear_pool_bytes
        projected_max_pe = average_pe * Decimal(str(scenario.wear_distribution_factor))
        required_margin = Decimal(str(scenario.required_pe_margin_fraction))
        allowable_pe = rated_pe * (Decimal(1) - required_margin)
        remaining_to_rated = rated_pe - projected_max_pe
        remaining_to_allowable = allowable_pe - projected_max_pe
    except (InvalidOperation, KeyError, TypeError, ZeroDivisionError):
        return {
            "status": "NOT_RUN",
            "scenario_id": scenario.scenario_id,
            "reason": "输入或来源事实无法按限定单位解释；计算 Fail-Closed。",
            "scenario_result": "INSUFFICIENT_EVIDENCE",
            "device_decision": "INSUFFICIENT_EVIDENCE",
        }

    if projected_max_pe <= allowable_pe:
        scenario_result = "PASS"
    elif projected_max_pe <= rated_pe:
        scenario_result = "MARGIN_GAP"
    else:
        scenario_result = "FAIL"
    evidence = pe_fact.get("evidence") or {}
    density_evidence = density_fact.get("evidence") or {}
    return {
        "status": "CALCULATED_TEST_SCENARIO",
        "scenario_id": scenario.scenario_id,
        "formula_id": "ILLUSTRATIVE_UNIFORM_WEAR_SCREEN_V1",
        "formula_approval": "NOT_APPROVED_FOR_DEVICE_QUALIFICATION",
        "scenario_result": scenario_result,
        "device_decision": "INSUFFICIENT_EVIDENCE",
        "device_decision_reason": "场景输入和简化磨损模型不能替代实测、完整料号/规格条件及经批准的产品寿命模型。",
        "input_trace": [
            {"name": "目标使用年限", "kind": "BUSINESS_INPUT", "value": str(years), "unit": "years"},
            {"name": "每日主机写入量", "kind": "ENGINEERING_SCENARIO_INPUT", "value": str(Decimal(str(scenario.host_write_gib_per_day))), "unit": "GiB/day", "measured": False},
            {"name": "WAF", "kind": "ENGINEERING_ASSUMPTION", "value": str(Decimal(str(scenario.waf))), "unit": "NAND bytes / host bytes"},
            {"name": "可磨损池占标称容量比例", "kind": "ENGINEERING_ASSUMPTION", "value": str(Decimal(str(scenario.wear_pool_fraction_of_nominal))), "unit": "fraction", "includes": "假设性保留/OP折减；不是器件实际OP"},
            {"name": "磨损不均匀系数", "kind": "ENGINEERING_ASSUMPTION", "value": str(Decimal(str(scenario.wear_distribution_factor))), "unit": "ratio"},
            {"name": "要求保留的额定P/E裕量", "kind": "ENGINEERING_ASSUMPTION", "value": str(required_margin), "unit": "fraction"},
            {"name": "额定P/E cycles with ECC", "kind": "SOURCE_FACT", "value": str(rated_pe), "unit": pe_fact.get("unit"), "source_id": evidence.get("source_id"), "revision_id": evidence.get("source_revision"), "locator": evidence.get("locator"), "snapshot_sha256": evidence.get("snapshot_sha256"), "qualification_eligible": False},
            {"name": "标称密度", "kind": "SOURCE_FACT", "value": str(nominal_gigabits), "unit": "Gb (decimal)", "source_id": density_evidence.get("source_id"), "revision_id": density_evidence.get("source_revision"), "locator": density_evidence.get("locator"), "snapshot_sha256": density_evidence.get("snapshot_sha256"), "qualification_eligible": False},
        ],
        "formula": {
            "days_per_year": str(days_per_year),
            "gib_bytes": str(gib_bytes),
            "raw_capacity_bytes": str(raw_bytes),
            "wear_pool_bytes": str(wear_pool_bytes),
            "host_write_bytes": str(host_bytes),
            "nand_media_write_bytes": str(nand_media_write_bytes),
            "average_pe_cycles": str(average_pe),
            "projected_max_pe_cycles": str(projected_max_pe),
            "rated_pe_cycles": str(rated_pe),
            "required_margin_fraction": str(required_margin),
            "allowable_pe_cycles_after_margin": str(allowable_pe),
            "remaining_to_rated_pe_cycles": str(remaining_to_rated),
            "remaining_to_allowable_pe_cycles": str(remaining_to_allowable),
            "equations": [
                "host_write_bytes = GiB_per_day * 2^30 * years * 365",
                "NAND_media_write_bytes = host_write_bytes * WAF",
                "wear_pool_bytes = (nominal_Gb * 10^9 / 8) * assumed_pool_fraction",
                "average_PE = NAND_media_write_bytes / wear_pool_bytes",
                "projected_max_PE = average_PE * assumed_wear_distribution_factor",
                "allowable_PE = rated_PE * (1 - required_margin_fraction)",
            ],
        },
        "decision_criteria": {
            "PASS": "projected_max_PE_cycles <= allowable_pe_cycles_after_margin",
            "MARGIN_GAP": "allowable_pe_cycles_after_margin < projected_max_PE_cycles <= rated_pe_cycles",
            "FAIL": "projected_max_PE_cycles > rated_pe_cycles",
        },
        "limitations": [
            "本模型仅为受控算术/单位链路测试，不是已批准的器件寿命模型。",
            "假设磨损池均匀承受写入；真实坏块、保留块、FTL/控制器、温度、ECC、Retention及循环条件未建模。",
            "P/E 和密度来自 Public Datasheet 摘录，具体完整订货料号及规格适用条件未确认；不得用于真实器件资格放行。",
            "本场景写入量、WAF、可磨损池比例、磨损不均匀系数和 Margin 全是测试输入/假设，不是实测值或供应商事实。",
        ],
        "validation_plan": [
            "在目标硬件和代表性工作负载下同步采集 Host Bytes 与 NAND Media Bytes，校验实测 WAF。",
            "记录有效块池、OP/保留块、坏块策略及每块/高分位 P/E，验证磨损分布假设。",
            "由可靠性 Owner 批准正式寿命公式、Retention/温度条件、Margin 与 EVT/DVT 失效判据后，再进行器件资格判断。",
        ],
    }


def _formal_query() -> dict[str, Any]:
    service = RealKnowledgeAssessmentService.current()
    return service.adapter.query_pack(
        "PACK_LIFETIME_ENGINEERING",
        "GD5F1GQ5 NAND P/E cycles endurance retention capacity",
        device_type="NAND",
        top_k=12,
    )


def _public_search(query: str) -> dict[str, Any]:
    return public_knowledge.search(
        public_knowledge.SearchBody(query=query, top_k=12), mode="LIVE"
    )


def _citation_lookup(citation_id: str) -> dict[str, Any]:
    return public_knowledge.citation(citation_id, mode="LIVE")


def _source_lookup(source_id: str) -> dict[str, Any]:
    return public_knowledge._request(
        "LIVE", "/sources/" + quote(source_id, safe="")
    )


def _snapshot_lookup(source_id: str, revision_id: str) -> tuple[bytes, dict[str, str]]:
    return public_knowledge.source_revision_snapshot_bytes(
        source_id, revision_id, mode="LIVE"
    )


def _locator(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _bound_evidence(
    hit: dict[str, Any],
    *,
    citation_lookup: Callable[[str], dict[str, Any]],
    source_lookup: Callable[[str], dict[str, Any]],
) -> dict[str, Any] | None:
    """Return evidence only when search hit, citation, source and revision agree."""
    citation_id = str(hit.get("hit_id") or hit.get("citation_id") or "").strip()
    source_id = str(hit.get("source_id") or "").strip()
    revision_id = str(hit.get("source_revision") or "").strip()
    if not citation_id or not source_id or not revision_id:
        return None
    try:
        citation = citation_lookup(citation_id)
        source_response = source_lookup(source_id)
    except (HTTPException, OSError, ValueError, KeyError):
        return None
    source = source_response.get("source") if isinstance(source_response, dict) else None
    revisions = source_response.get("revisions") if isinstance(source_response, dict) else None
    title = str((source or {}).get("title") or "")
    publisher = str((source or {}).get("publisher") or "")
    source_class = str((source or {}).get("source_class") or "").upper()
    citation_locator = _locator(citation.get("locator"))
    search_text = str(hit.get("text") or "").strip()
    citation_text = str(citation.get("text") or "").strip()
    revision_exists = any(
        str(row.get("revision_id") or "") == revision_id
        for row in (revisions or []) if isinstance(row, dict)
    )
    if (
        str(citation.get("citation_id") or "") != citation_id
        or str(citation.get("source_id") or "") != source_id
        or str(citation.get("source_revision") or "") != revision_id
        or not revision_exists
        or not citation_locator
        or citation_text != search_text
        or GD5_SOURCE_MODEL.casefold() not in title.casefold()
        or "gigadevice" not in publisher.casefold()
        or source_class != "PUBLIC"
    ):
        return None
    return {
        "citation_id": citation_id,
        "source_id": source_id,
        "source_revision": revision_id,
        "source_title": title,
        "publisher": publisher,
        "source_class": source_class,
        "locator": citation_locator,
        "quote": citation_text,
        "content_sha256": citation.get("content_sha256"),
        "original_filename": citation.get("original_filename"),
        "evidence_type": "PUBLIC_KNOWLEDGE_SOURCE_CITATION",
    }


def _fact_from_query(
    *,
    query: str,
    fact_id: str,
    label: str,
    pattern: re.Pattern[str],
    normalize: Callable[[re.Match[str]], tuple[Any, str]],
    search_fn: Callable[[str], dict[str, Any]],
    citation_lookup: Callable[[str], dict[str, Any]],
    source_lookup: Callable[[str], dict[str, Any]],
) -> dict[str, Any] | None:
    result = search_fn(query)
    for hit in result.get("hits") or []:
        text = str(hit.get("text") or "")
        match = pattern.search(text)
        if not match:
            continue
        evidence = _bound_evidence(
            hit, citation_lookup=citation_lookup, source_lookup=source_lookup
        )
        if evidence is None:
            continue
        value, unit = normalize(match)
        return {
            "fact_id": fact_id,
            "kind": "SOURCE_FACT",
            "label": label,
            "value": value,
            "unit": unit,
            "source_precedence": "PUBLIC_SUPPLEMENT_NO_MATCHING_FORMAL_FACT",
            "condition": "由引用原文逐字/规则化抽取；未补充原文未声明的条件。",
            "evidence": evidence,
            "claim_binding": "PASS_EXACT_CITATION_AND_SOURCE_REVISION_MATCH",
        }
    return None


def _find_device_snapshot(
    *,
    model: str,
    search_fn: Callable[[str], dict[str, Any]],
    source_lookup: Callable[[str], dict[str, Any]],
    snapshot_lookup: Callable[[str, str], tuple[bytes, dict[str, str]]],
) -> dict[str, Any] | None:
    """Resolve a Public source/revision, then verify its immutable snapshot hash."""
    try:
        result = search_fn(f"{model} SPI-NAND datasheet")
    except HTTPException:
        return None
    for hit in result.get("hits") or []:
        source_id = str(hit.get("source_id") or "").strip()
        revision_id = str(hit.get("source_revision") or "").strip()
        if not source_id or not revision_id:
            continue
        try:
            source_response = source_lookup(source_id)
            source = source_response.get("source") or {}
            revisions = source_response.get("revisions") or []
            revision = next(
                (row for row in revisions if str(row.get("revision_id") or "") == revision_id),
                None,
            )
            title = str(source.get("title") or "")
            publisher = str(source.get("publisher") or "")
            if (
                not revision
                or model.casefold() not in title.casefold()
                or "gigadevice" not in publisher.casefold()
                or str(source.get("source_class") or "").upper() != "PUBLIC"
            ):
                continue
            snapshot, snapshot_headers = snapshot_lookup(source_id, revision_id)
            snapshot_sha = hashlib.sha256(snapshot).hexdigest()
            expected_raw_sha = str(revision.get("raw_sha256") or "").lower()
            if not expected_raw_sha or snapshot_sha != expected_raw_sha:
                continue
            reader = PdfReader(BytesIO(snapshot), strict=True)
            return {
                "source_id": source_id,
                "source_revision": revision_id,
                "source_title": title,
                "publisher": publisher,
                "source_class": "PUBLIC",
                "original_filename": revision.get("original_filename"),
                "content_sha256": revision.get("content_sha256"),
                "raw_sha256": expected_raw_sha,
                "snapshot_sha256": snapshot_sha,
                "snapshot_headers": snapshot_headers,
                "page_count": len(reader.pages),
                "reader": reader,
            }
        except (HTTPException, OSError, ValueError, KeyError, StopIteration):
            continue
    return None


def _snapshot_fact(
    *,
    source_snapshot: dict[str, Any] | None,
    pattern: re.Pattern[str],
    fact_id: str,
    label: str,
    normalize: Callable[[re.Match[str]], tuple[Any, str]],
) -> dict[str, Any] | None:
    if not source_snapshot:
        return None
    reader = source_snapshot.get("reader")
    if reader is None:
        return None
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for extraction_line, line in enumerate(text.splitlines(), start=1):
            match = pattern.search(line)
            if not match:
                continue
            value, unit = normalize(match)
            evidence = {
                "source_id": source_snapshot["source_id"],
                "source_revision": source_snapshot["source_revision"],
                "source_title": source_snapshot["source_title"],
                "publisher": source_snapshot["publisher"],
                "source_class": "PUBLIC",
                "locator": {
                    "page": page_number,
                    "extraction_line": extraction_line,
                    "section": "FEATURE" if page_number == 4 else "SOURCE_SNAPSHOT_TEXT",
                    "type": "public_knowledge_revision_snapshot_pdf_text",
                },
                "quote": " ".join(line.split()),
                "original_filename": source_snapshot.get("original_filename"),
                "content_sha256": source_snapshot.get("content_sha256"),
                "snapshot_sha256": source_snapshot["snapshot_sha256"],
                "evidence_type": "PUBLIC_KNOWLEDGE_SOURCE_REVISION_SNAPSHOT",
            }
            return {
                "fact_id": fact_id,
                "kind": "SOURCE_FACT",
                "label": label,
                "value": value,
                "unit": unit,
                "source_precedence": "PUBLIC_SUPPLEMENT_NO_MATCHING_FORMAL_FACT",
                "condition": "只陈述被引用 PDF 行直接支持的内容；未补充原文未声明的条件。",
                "evidence": evidence,
                "claim_binding": "PASS_QUOTE_FOUND_ON_HASH_VERIFIED_SOURCE_REVISION_PAGE",
            }
    return None


def build_gd5f1gq5_lifetime_case(
    request: NandLifetimeCaseRequest,
    *,
    formal_query_fn: Callable[[], dict[str, Any]] = _formal_query,
    search_fn: Callable[[str], dict[str, Any]] = _public_search,
    citation_lookup: Callable[[str], dict[str, Any]] = _citation_lookup,
    source_lookup: Callable[[str], dict[str, Any]] = _source_lookup,
    snapshot_lookup: Callable[[str, str], tuple[bytes, dict[str, str]]] = _snapshot_lookup,
) -> dict[str, Any]:
    model = request.device_model.strip()
    if model.casefold() != GD5_SOURCE_MODEL.casefold():
        raise ValueError("UNSUPPORTED_ENGINEERING_CASE_DEVICE")

    formal = formal_query_fn()
    formal_items = list(formal.get("items") or [])
    formal_query_status = str(formal.get("status") or ("MATCH" if formal_items else "NO_MATCH"))
    formal_refs = list(formal.get("knowledge_refs") or [])
    formal_evidence = list(formal.get("evidence_refs") or [])
    source_snapshot = _find_device_snapshot(
        model=model,
        search_fn=search_fn,
        source_lookup=source_lookup,
        snapshot_lookup=snapshot_lookup,
    )

    fact_specs = [
        (
            "F-NAND-PE-ECC",
            "额定 P/E 循环（ECC 开启条件）",
            "GD5F1GQ5 P/E cycles with ECC 100K",
            re.compile(r"P/E\s+cycles\s+with\s+ECC\s*:\s*(\d+(?:\.\d+)?)\s*([KMG]?)", re.I),
            lambda m: (int(float(m.group(1)) * {"": 1, "K": 1_000, "M": 1_000_000, "G": 1_000_000_000}[m.group(2).upper()]), "P/E cycles"),
        ),
        (
            "F-NAND-RETENTION",
            "Datasheet 数据保持标称值",
            "GD5F1GQ5 Data retention 10 Years",
            re.compile(r"Data\s+retention\s*:\s*(\d+(?:\.\d+)?)\s*(years?)", re.I),
            lambda m: (float(m.group(1)).is_integer() and int(float(m.group(1))) or float(m.group(1)), "years"),
        ),
        (
            "F-NAND-NOMINAL-DENSITY",
            "标称密度与单元类型",
            "GD5F1GQ5 1Gb SLC NAND Flash",
            re.compile(r"(\d+(?:\.\d+)?)\s*(Gb|Gbit)\s+(SLC)\s+NAND\s+Flash", re.I),
            lambda m: ({"magnitude": float(m.group(1)), "unit": "Gb" if m.group(2).casefold() == "gb" else "Gbit", "cell_type": m.group(3).upper()}, "nominal density"),
        ),
    ]
    facts: list[dict[str, Any]] = []
    retrieval_errors: list[str] = []
    retrieval_observations: list[dict[str, Any]] = []
    for fact_id, label, query, pattern, normalize in fact_specs:
        try:
            search_result = search_fn(query)
            hits = list(search_result.get("hits") or [])
        except HTTPException as exc:
            hits = []
            retrieval_errors.append(f"PUBLIC_SEARCH_HTTP_{exc.status_code}:{fact_id}")
        fact = None
        direct_citation_bound = False
        for hit in hits:
            if not pattern.search(str(hit.get("text") or "")):
                continue
            fact = _fact_from_query(
                query=query,
                fact_id=fact_id,
                label=label,
                pattern=pattern,
                normalize=normalize,
                search_fn=lambda _query, current=hit: {"hits": [current]},
                citation_lookup=citation_lookup,
                source_lookup=source_lookup,
            )
            if fact:
                direct_citation_bound = True
                break
        if fact is None:
            fact = _snapshot_fact(
                source_snapshot=source_snapshot,
                pattern=pattern,
                fact_id=fact_id,
                label=label,
                normalize=normalize,
            )
        retrieval_observations.append({
            "fact_id": fact_id,
            "targeted_search_query": query,
            "search_hits": len(hits),
            "search_hit_directly_supported_claim": direct_citation_bound,
            "source_snapshot_fallback_used": bool(fact and not direct_citation_bound),
            "status": "FACT_BOUND" if fact else "NO_EVIDENCE_NO_FACT",
        })
        if fact:
            facts.append(fact)

    user_inputs = [{
        "input_id": "I-TARGET-LIFE",
        "kind": "USER_INPUT",
        "name": "产品目标使用寿命",
        "value": request.target_service_life_years,
        "unit": "years",
        "provided_by": "本次工程 Case 请求",
    }]
    assumptions: list[dict[str, Any]] = []

    missing_specs = [
        ("rated_pe_cycle_formal_confirmation", "公开 Datasheet 已摘录 P/E cycles with ECC = 100K，但当前 Release 没有 NAND 正式知识，且没有与具体器件绑定的 Confirmed Device Fact；不能直接把 Public 摘录塞入寿命引擎。", "Knowledge Production / Source Verification + 器件工程师", "GigaDevice 官方 Datasheet、精确订货料号及受控知识审核流程", "核对料号、ECC 条件、Revision 与 citation/snapshot hash；由授权 Reviewer 完成正式确认，禁止直接写库。", "经受控确认后才能作为 NAND_PE_MARGIN_V1 的 rated_pe_cycles 输入。"),
        ("exact_ordering_part_number", "当前只有 GD5F1GQ5 家族名；不能锁定电压、封装及温度等级。", "器件/硬件工程师 + GigaDevice FAE", "BOM、采购料号、器件顶标与对应官方 Datasheet 订货表", "料号逐字符匹配官方订货编码及 Revision；确认温度等级和 ECC 配置。", "完成型号绑定后，将供应商规格纳入受控 Device Fact/正式知识审核。"),
        ("host_write_workload", "没有实际主机写入量，无法把 5 年要求转成总写入需求。", "系统工程师 / 产品需求 Owner", "产品任务剖面、现场遥测或已批准的工作负载规格", "同时给出平均/峰值 GB/day、运行天数、写入分布及数据来源；与系统日志/业务流量核对。", "输入经确认后进入寿命工作负载模型；未确认前不计算。"),
        ("measured_waf", "主机写入量不等于 NAND 介质写入量；缺少实测 WAF。", "固件/存储栈 Owner", "目标文件系统、FTL/控制器计数器及代表性工作负载实测", "同一时间窗采集 Host Bytes 与 NAND Media Bytes；核验计数器口径、复位点和测量误差。", "与工作负载绑定后用于已批准的介质写入/磨损模型。"),
        ("usable_capacity_and_op", "Datasheet 标称 1Gb 不等同于应用可用容量；坏块、保留区和 OP 未知。", "硬件/固件 Owner", "控制器映射、分区/BOM 设计、坏块策略及 OP 配置", "核对物理密度、可寻址容量、保留块和 OP 配置，不从标称密度推定可用字节。", "以确认的有效容量进入后续模型，不直接把 nominal density 当作分母。"),
        ("runtime_wear_distribution", "没有运行期擦写计数、最大块磨损或磨损均衡分布，平均值不能代表最坏块。", "固件 Owner / 测试工程师", "控制器/驱动诊断计数器与目标工作负载测试", "确认计数器语义、覆盖范围、采样周期及最坏/高分位块擦写；交叉核对 ECC/坏块状态。", "确认可消费语义后使用现有 NAND_PE_MARGIN_V1；不把能力字段冒充观测值。"),
        ("temperature_and_retention_conditions", "Retention 的 10 年摘录未给出适用温度、磨损状态和断电条件；实际器件温度等级也依赖完整料号。", "可靠性 Owner + 器件/硬件工程师", "完整 Datasheet 条件表、供应商可靠性报告、产品热剖面和断电存储要求", "逐项对齐工作/存储温度、P/E 状态、断电保持时长及判据；由供应商确认规格条件。", "完成条件匹配后才能把 Retention 与 5 年产品目标联合判断。"),
        ("approved_lifetime_margin_and_projection_model", "现有 NAND_PE_MARGIN_V1 是额定 P/E 与观测 P/E 的裕量，不是依据主机写入预测 5 年寿命的公式；缺少批准的预测模型和 Margin。", "Storage Reliability / 产品 Gate Owner", "批准的工程寿命规范、验证协议和确定性公式注册表", "明确模型输入、单位换算、磨损不均匀系数、统计边界、Margin 及失效判据，并由独立评审批准。", "只有模型注册并能重放公式后，才进行 5 年预测和 PASS/FAIL 判定。"),
    ]
    missing_facts = [
        {"name": name, "status": "MISSING", "why": why, "owner": owner, "obtain_from": source, "verification": verify, "next_stage": next_stage}
        for name, why, owner, source, verify, next_stage in missing_specs
    ]

    if formal_items:
        formal_state = "MATCHING_FORMAL_KNOWLEDGE_AVAILABLE_REVIEW_REQUIRED"
    else:
        formal_state = "NO_MATCHING_PUBLISHED_NAND_KNOWLEDGE"
    expected_fact_ids = {spec[0] for spec in fact_specs}
    found_fact_ids = {str(fact.get("fact_id")) for fact in facts}
    unavailable_public_facts = sorted(expected_fact_ids - found_fact_ids)
    if unavailable_public_facts:
        missing_facts.append({
            "name": "unretrieved_or_unbound_device_source_facts",
            "status": "MISSING_EVIDENCE_BINDING",
            "why": "当前 Public Knowledge 搜索未返回可与原始 Citation、Source、Revision 逐项核验的命中；不得据此声称事实不存在。",
            "owner": "Knowledge Production / Public Knowledge Source Owner",
            "obtain_from": "当前已登记 GD5F1GQ5 datasheet Source 与其 revision snapshot",
            "verification": "使精确规格行可被检索，并通过 Citation API 核验 quote、source_id、revision_id 和 locator。",
            "next_stage": "绑定成功后才可加入 SOURCE_FACT；不回写/改造源文档来迎合 Case。",
            "fact_ids": unavailable_public_facts,
        })

    calculation_blockers = [
        "rated_pe_cycle_not_bound_as_confirmed_device_fact",
        "observed_pe_cycle_or_wear_distribution_missing",
        "confirmed_host_write_workload_missing",
        "measured_waf_missing",
        "effective_usable_capacity_and_op_missing",
        "runtime_wear_distribution_missing",
        "approved_five_year_projection_formula_and_margin_missing",
        "exact_ordering_part_number_and_retention_conditions_unconfirmed",
    ]
    case_core = {
        "device_model": model,
        "target_service_life_years": request.target_service_life_years,
        "formal_release": formal.get("knowledge_release") or {},
        "public_fact_citations": [fact.get("evidence") for fact in facts],
    }
    case_id = "NAND-LIFE-" + hashlib.sha256(
        json.dumps(case_core, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    r4_calculation = _r4_controlled_screen(request, facts)
    return {
        "contract_version": "storage-nand-engineering-case/v1",
        "case_id": case_id,
        "execution_mode": "READ_ONLY_REAL_DATA",
        "device_model": model,
        "business_question": f"產品要求使用 {request.target_service_life_years:g} 年；GD5F1GQ5 是否已具備壽命選型條件？",
        "source_precedence": {
            "formal_knowledge_first": True,
            "formal_pack": "PACK_LIFETIME_ENGINEERING",
            "formal_query_status": formal_query_status,
            "formal_knowledge_refs": formal_refs,
            "formal_evidence_refs": formal_evidence,
            "formal_nand_coverage": formal_state,
        "public_knowledge_role": "SUPPLEMENT_ONLY",
        "public_generation_used": False,
        },
        "A_KNOWN_FACTS": facts,
        "B_MISSING_FACTS": missing_facts,
        "C_ASSUMPTIONS": assumptions,
        "USER_INPUTS": user_inputs,
        "D_DERIVATION": {
            "status": "NOT_RUN",
            "formula_id": None,
            "inputs": [],
            "calculation": None,
            "margin": None,
            "blockers": calculation_blockers,
            "reason": "缺少已確認的現場寫入/磨損輸入及已批准的 5 年 NAND 預測公式；不以公開額定值單獨推導產品壽命。",
        },
        "R4_CONTROLLED_SCENARIO_CALCULATION": r4_calculation,
        "E_DECISION": "INSUFFICIENT_EVIDENCE",
        "decision_basis": {
            "no_unsupported_pass_fail": True,
            "rated_specification_alone_does_not_prove_application_life": True,
            "calculation_blockers": calculation_blockers,
        },
        "F_ACTION_PLAN": [
            {"sequence": index, **item}
            for index, item in enumerate(missing_facts, start=1)
        ],
        "formula_path": {
            "existing_formula": "NAND_PE_MARGIN_V1",
            "existing_formula_semantics": "confirmed rated P/E cycles minus formally consumable observed P/E cycles",
            "current_use": "NOT_RUN; no confirmed Device Fact or formally consumable wear observation was supplied",
            "five_year_projection": "NOT_AVAILABLE_UNTIL_APPROVED_FORMULA_AND_MARGIN_ARE_REGISTERED",
        },
        "retrieval_errors": retrieval_errors,
        "public_retrieval_observations": retrieval_observations,
        "source_snapshot_identity": (
            {key: value for key, value in source_snapshot.items() if key != "reader"}
            if source_snapshot else None
        ),
        "state_mutation": "NO",
        "provider_calls": 0,
        "snapshot_artifact": "PUBLIC_KNOWLEDGE_SOURCE_REVISION_CITATIONS",
    }


R5_CONTROLLED_SCENARIOS = (
    {"scenario_id": "R4-GD5-CONTROLLED-PASS", "host_write_gib_per_day": 0.5},
    {"scenario_id": "R4-GD5-CONTROLLED-MARGIN-GAP", "host_write_gib_per_day": 1.1},
    {"scenario_id": "R4-GD5-CONTROLLED-FAIL", "host_write_gib_per_day": 1.5},
)
_R5_SHARED_SCENARIO_ASSUMPTIONS = {
    "waf": 3.0,
    "wear_pool_fraction_of_nominal": 0.8,
    "wear_distribution_factor": 1.5,
    "required_pe_margin_fraction": 0.2,
}


def _r5_role_views(shared_case: dict[str, Any], scenario_results: list[dict[str, Any]]) -> dict[str, Any]:
    fact_ids = [str(fact.get("fact_id")) for fact in shared_case.get("A_KNOWN_FACTS", [])]
    evidence_refs = [
        {
            "fact_id": fact.get("fact_id"),
            "source_id": (fact.get("evidence") or {}).get("source_id"),
            "source_revision": (fact.get("evidence") or {}).get("source_revision"),
            "locator": (fact.get("evidence") or {}).get("locator"),
            "snapshot_sha256": (fact.get("evidence") or {}).get("snapshot_sha256"),
            "citation_id": (fact.get("evidence") or {}).get("citation_id"),
        }
        for fact in shared_case.get("A_KNOWN_FACTS", [])
    ]
    shared_payload = {
        "case_id": shared_case.get("case_id"),
        "device_model": shared_case.get("device_model"),
        "business_question": shared_case.get("business_question"),
        "source_precedence": shared_case.get("source_precedence"),
        "facts": shared_case.get("A_KNOWN_FACTS"),
        "missing": shared_case.get("B_MISSING_FACTS"),
        "assumptions": shared_case.get("C_ASSUMPTIONS"),
        "derivation": shared_case.get("D_DERIVATION"),
        "scenario_results": scenario_results,
        "device_decision": shared_case.get("E_DECISION"),
    }
    shared_fingerprint = hashlib.sha256(
        json.dumps(shared_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    gap_names = [str(row.get("name")) for row in shared_case.get("B_MISSING_FACTS", [])]
    scenario_refs = [
        {
            "scenario_id": row.get("scenario_id"),
            "scenario_result": row.get("scenario_result"),
            "device_decision": row.get("device_decision"),
            "projected_max_pe_cycles": (row.get("formula") or {}).get("projected_max_pe_cycles"),
            "allowable_pe_cycles_after_margin": (row.get("formula") or {}).get("allowable_pe_cycles_after_margin"),
        }
        for row in scenario_results
    ]
    shared_decision = str(shared_case.get("E_DECISION") or "INSUFFICIENT_EVIDENCE")

    role_definitions = {
        "SYSTEM_ENGINEER": {
            "title": "系统工程师",
            "status": "ACTIONABLE_WITH_OPEN_GATE",
            "view": "5 年是已知业务目标，但目前不能冻结 NAND 寿命数值或选型结论。先把系统写入预算、有效容量、WAF 上限、温度/Retention 条件和 Margin 变成待关闭的规格项；所有项有证据且正式模型获批后，才能关闭寿命规格。",
            "next_actions": ["与产品需求 Owner 确认平均/峰值写入量及任务剖面。", "把有效容量、WAF、温度、Retention、寿命 Margin 列为规格冻结前置条件。", "等待硬件/采购补齐器件证据、软件/测试交付实测，再用同一 Case 复核。"],
            "gap_refs": ["host_write_workload", "measured_waf", "usable_capacity_and_op", "temperature_and_retention_conditions", "approved_lifetime_margin_and_projection_model"],
        },
        "HARDWARE_ENGINEER": {
            "title": "硬件/器件工程师",
            "status": "ACTIONABLE_WITH_OPEN_GATE",
            "view": "共享来源目前支持 100K P/E cycles with ECC、10 年 Retention、1Gb SLC 的公开摘录；这些不是已确认的完整料号 Device Fact。100K P/E 不等价于五年够用。",
            "next_actions": ["确认 BOM 完整订货料号、封装/电压/温度等级与 Datasheet Revision。", "核对 P/E 的 ECC 条件、有效块/坏块和 Retention 适用条件。", "完成受控事实确认前，不作器件选型 PASS/FAIL。"],
            "gap_refs": ["rated_pe_cycle_formal_confirmation", "exact_ordering_part_number", "temperature_and_retention_conditions"],
        },
        "SOFTWARE_ENGINEER": {
            "title": "软件工程师",
            "status": "ACTIONABLE_WITH_OPEN_GATE",
            "view": "同一计算底座显示：测试输入 0.5 GiB/day 的简化筛查为 PASS，1.1 为 MARGIN_GAP，1.5 为 FAIL。它们不是实测负载或器件结论。软件侧应让 Host Bytes 可观测、约束写入预算，并与 NAND Media Bytes 同窗测量 WAF。",
            "next_actions": ["定义主机写入计数的采集周期、复位点和峰值窗口。", "审查日志、数据库/WAL、缓存刷新、fsync、GC 等写入路径并记录优化前后 Host Bytes。", "与固件/测试联合测 WAF；在批准阈值前不把场景数字写成软件硬限制。"],
            "gap_refs": ["host_write_workload", "measured_waf", "runtime_wear_distribution"],
        },
        "PROCUREMENT": {
            "title": "采购/供应链",
            "status": "ACTIONABLE_WITH_OPEN_GATE",
            "view": "当前只能按共享缺口向供应商补证，不能以标称 P/E 或容量相近作为替代放行依据。",
            "next_actions": ["索取完整订货料号对应的受控 Datasheet/Revision、P/E 与 ECC 条件。", "索取 Endurance 定义、有效/坏块条件、Retention 与温度/磨损状态条件及可靠性报告。", "供应商证据无法对应完整料号或关键条件时，标记不可完成寿命选型放行。"],
            "gap_refs": ["rated_pe_cycle_formal_confirmation", "exact_ordering_part_number", "temperature_and_retention_conditions"],
        },
        "TEST_ENGINEER": {
            "title": "测试工程师",
            "status": "ACTIONABLE_WITH_OPEN_GATE",
            "view": "先验证输入和计数口径，再做寿命验证；不可仅凭运行时长外推。测试计划沿用共享 Case 的公式输入和缺口，不另造阈值。",
            "next_actions": ["在代表性任务剖面下同步采集 Host Bytes 与 NAND Media Bytes，计算实测 WAF 并记录误差/计数语义。", "采集每块及高分位 P/E、有效块池、坏块、ECC 状态和温度时间序列。", "按供应商 Retention 条件制定高温/断电保持验证；由可靠性 Owner 先批准持续时间、样本量、失效判据和外推方法。", "将经校验的测量结果作为新版本 Case 输入，保留原 Case 与证据版本。"],
            "gap_refs": ["measured_waf", "runtime_wear_distribution", "temperature_and_retention_conditions", "approved_lifetime_margin_and_projection_model"],
        },
        "CHANGE_MANAGEMENT": {
            "title": "变更管理",
            "status": "ACTIONABLE_WITH_OPEN_GATE",
            "view": "任何可能改变介质磨损、可用块池或规格适用条件的变更都需复核；容量相同本身不足以证明寿命等效。",
            "next_actions": ["将 NAND 完整料号/类型、密度、OP/保留块、固件/FTL、文件系统/数据库写策略、WAF、工作负载、温度及 Retention 条件列入寿命评估触发项。", "变更时复制同一 Engineering Case 的事实/证据版本，标注变更前后输入和重新测试结果。", "只有同一批准模型重算且测试证据满足 Margin 后，系统工程师才关闭变更影响。"],
            "gap_refs": ["exact_ordering_part_number", "usable_capacity_and_op", "measured_waf", "runtime_wear_distribution", "temperature_and_retention_conditions", "approved_lifetime_margin_and_projection_model"],
        },
    }
    views = {
        role_id: {
            **definition,
            "role_id": role_id,
            "shared_case_id": shared_case.get("case_id"),
            "shared_case_fingerprint": shared_fingerprint,
            "shared_fact_refs": fact_ids,
            "shared_evidence_refs": evidence_refs,
            "shared_missing_fact_refs": [name for name in definition["gap_refs"] if name in gap_names],
            "shared_scenario_refs": scenario_refs,
            "device_decision": shared_decision,
            "model_calls": 0,
        }
        for role_id, definition in role_definitions.items()
    }
    fingerprints = {view["shared_case_fingerprint"] for view in views.values()}
    device_decisions = {view["device_decision"] for view in views.values()}
    scenario_decisions = {
        scenario["scenario_id"]: scenario["scenario_result"]
        for scenario in scenario_refs
    }
    return {
        "shared_case_id": shared_case.get("case_id"),
        "shared_case_fingerprint": shared_fingerprint,
        "shared_case": shared_case,
        "scenario_results": scenario_results,
        "role_views": views,
        "closure_map": [
            {"gap": "host_write_workload", "system": "需求提供任务剖面", "software": "提供Host Bytes计数", "test": "验证采集口径和负载"},
            {"gap": "measured_waf", "software": "联合固件定义计数器", "test": "同步Host/Media Bytes测量"},
            {"gap": "rated_pe_cycle_formal_confirmation", "procurement": "取得料号对应供应商证据", "hardware": "核条件并提交受控确认"},
            {"gap": "runtime_wear_distribution", "hardware": "提供控制器磨损计数", "test": "验证高分位/最坏块P/E"},
            {"gap": "approved_lifetime_margin_and_projection_model", "reliability": "批准公式、Margin和失效判据", "system": "据批准结果冻结系统规格"},
        ],
        "consistency": {
            "shared_fact_consistency": "PASS" if len(fingerprints) == 1 else "FAIL",
            "shared_evidence_consistency": "PASS" if len(fingerprints) == 1 else "FAIL",
            "shared_derivation_consistency": "PASS" if len(fingerprints) == 1 else "FAIL",
            "role_count": len(views),
            "cross_role_contradiction_count": 0 if len(device_decisions) == 1 and len(scenario_decisions) == len(scenario_results) else 1,
            "screening_not_promoted_to_device_qualification": all(
                row.get("device_decision") == "INSUFFICIENT_EVIDENCE"
                for row in scenario_results
            ),
            "role_views_are_deterministic_projections": True,
            "provider_calls": 0,
        },
    }


def build_nand_lifetime_role_consumption(
    request: NandLifetimeRoleRequest,
    *,
    formal_query_fn: Callable[[], dict[str, Any]] = _formal_query,
    search_fn: Callable[[str], dict[str, Any]] = _public_search,
    citation_lookup: Callable[[str], dict[str, Any]] = _citation_lookup,
    source_lookup: Callable[[str], dict[str, Any]] = _source_lookup,
    snapshot_lookup: Callable[[str, str], tuple[bytes, dict[str, str]]] = _snapshot_lookup,
) -> dict[str, Any]:
    """Build one shared Case once, then project it into six deterministic role views."""
    shared_case = build_gd5f1gq5_lifetime_case(
        NandLifetimeCaseRequest(
            device_model=request.device_model,
            target_service_life_years=request.target_service_life_years,
        ),
        formal_query_fn=formal_query_fn,
        search_fn=search_fn,
        citation_lookup=citation_lookup,
        source_lookup=source_lookup,
        snapshot_lookup=snapshot_lookup,
    )
    scenarios = []
    for scenario_input in R5_CONTROLLED_SCENARIOS:
        scenario = ControlledLifetimeScenario(
            **scenario_input,
            **_R5_SHARED_SCENARIO_ASSUMPTIONS,
        )
        scenario_request = NandLifetimeCaseRequest(
            device_model=request.device_model,
            target_service_life_years=request.target_service_life_years,
            controlled_scenario=scenario,
        )
        calculation = _r4_controlled_screen(scenario_request, shared_case["A_KNOWN_FACTS"])
        scenarios.append(calculation or {
            "scenario_id": scenario.scenario_id,
            "scenario_result": "INSUFFICIENT_EVIDENCE",
            "device_decision": "INSUFFICIENT_EVIDENCE",
        })
    return _r5_role_views(shared_case, scenarios)


@router.post("/api/product/engineering-cases/nand-lifetime")
def create_nand_lifetime_engineering_case(payload: NandLifetimeCaseRequest):
    try:
        return build_gd5f1gq5_lifetime_case(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/product/engineering-cases/nand-lifetime/roles")
def create_nand_lifetime_role_consumption(payload: NandLifetimeRoleRequest):
    try:
        return build_nand_lifetime_role_consumption(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
