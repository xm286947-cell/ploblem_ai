"""Frozen Storage MVP RC1 product parameter baseline.

This module is Storage-owned product semantics. It does not change Runtime provider
behavior and does not belong to Unified Knowledge. P03/P05/P08 consume this same
baseline so required slots cannot disappear merely because a datasheet omits a value.
"""
from __future__ import annotations

from typing import Any

KEY_SPEC = "KEY_SPEC"
KEY_DIAGNOSTIC = "KEY_DIAGNOSTIC"
COMPREHENSIVE = "COMPREHENSIVE"

GROUP_LABELS = {
    KEY_SPEC: "关键说明参数",
    KEY_DIAGNOSTIC: "关键诊断参数",
    COMPREHENSIVE: "全面参数",
}


def _f(key: str, label: str, group: str, level: str = "MUST", aliases: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "canonical_name": key,
        "parameter_name": label,
        "group": group,
        "group_label": GROUP_LABELS[group],
        "requirement_level": level,
        "aliases": list(dict.fromkeys((key, *aliases))),
    }


BASELINES: dict[str, list[dict[str, Any]]] = {
    "eMMC": [
        _f("capacity", "容量（Capacity）", KEY_SPEC),
        _f("emmc_version", "eMMC 规范版本（eMMC Specification Version）", KEY_SPEC, aliases=("interface",)),
        _f("operating_temperature", "工作温度范围（Operating Temperature Range）", KEY_SPEC),
        _f("endurance_condition", "耐久度 / 使用模型（Endurance / Usage Model）", KEY_SPEC, aliases=("pe_cycle", "pe_cycles")),
        _f("enhanced_user_data_area", "增强区域（Enhanced User Data Area / Enhanced Attribute）", KEY_SPEC),
        _f("reliable_write", "可靠写入（Reliable Write / Write Reliability）", KEY_SPEC, aliases=("write_reliability",)),
        _f("cache", "缓存（Cache）", KEY_SPEC),
        _f("bkops", "后台操作能力（Background Operations / BKOPS）", KEY_SPEC),
        _f("bus_width", "器件组织 / 总线宽度（Device Organization / Bus Width）", KEY_SPEC, "SHOULD"),
        _f("native_nand_type", "NAND 技术类型（NAND Technology）", KEY_SPEC, "SHOULD", aliases=("nand_type", "cell_type")),
        _f("life_time_a", "设备寿命估计 A（Device Life Time Estimation Type A）", KEY_DIAGNOSTIC, aliases=("device_life_time_est_typ_a",)),
        _f("life_time_b", "设备寿命估计 B（Device Life Time Estimation Type B）", KEY_DIAGNOSTIC, aliases=("device_life_time_est_typ_b",)),
        _f("pre_eol", "预寿命结束信息（Pre EOL Information）", KEY_DIAGNOSTIC, aliases=("pre_eol_info",)),
        _f("ext_csd_health_report", "健康信息读取能力（Device Health / EXT_CSD Health Reporting）", KEY_DIAGNOSTIC, aliases=("health_report",)),
        _f("bkops_status", "后台操作状态读取（BKOPS Status / Need）", KEY_DIAGNOSTIC, "SHOULD"),
        _f("health_read_method", "寿命/健康读取方法与数据源（Read Method / Data Source）", KEY_DIAGNOSTIC, aliases=("access_method",)),
    ],
    "SSD": [
        _f("capacity", "容量（Capacity）", KEY_SPEC),
        _f("interface_protocol", "接口 / 协议（Interface / Protocol）", KEY_SPEC, aliases=("interface", "protocol")),
        _f("tbw", "总写入字节数寿命（Total Bytes Written / TBW）", KEY_SPEC),
        _f("dwpd", "每日全盘写入（Drive Writes Per Day / DWPD）", KEY_SPEC, "SHOULD"),
        _f("endurance_workload", "耐久度工作负载 / 保修条件（Endurance Workload / Warranty Condition）", KEY_SPEC, aliases=("endurance_class", "warranty_write_limit")),
        _f("operating_temperature", "工作温度范围（Operating Temperature Range）", KEY_SPEC),
        _f("nand_type", "NAND 类型（NAND Type）", KEY_SPEC, "SHOULD", aliases=("cell_type",)),
        _f("over_provisioning", "预留空间（Over Provisioning）", KEY_SPEC, "SHOULD"),
        _f("percentage_used", "已用百分比（Percentage Used）", KEY_DIAGNOSTIC),
        _f("available_spare", "可用备用空间（Available Spare）", KEY_DIAGNOSTIC),
        _f("spare_threshold", "备用空间阈值（Available Spare Threshold）", KEY_DIAGNOSTIC),
        _f("critical_warning", "关键警告（Critical Warning）", KEY_DIAGNOSTIC),
        _f("data_units_written", "数据单元写入（Data Units Written）", KEY_DIAGNOSTIC),
        _f("media_errors", "介质和数据完整性错误（Media and Data Integrity Errors）", KEY_DIAGNOSTIC),
        _f("smart_health", "SMART / NVMe 健康日志读取能力（SMART / NVMe Health Log）", KEY_DIAGNOSTIC),
        _f("power_on_hours", "通电时间（Power On Hours）", KEY_DIAGNOSTIC, "SHOULD"),
        _f("unsafe_shutdowns", "非正常关机次数（Unsafe Shutdowns）", KEY_DIAGNOSTIC, "SHOULD"),
    ],
    "NAND Flash": [
        _f("nand_type", "NAND 类型（SLC / MLC / TLC / QLC）", KEY_SPEC, aliases=("cell_type",)),
        _f("capacity", "容量 / 密度（Capacity / Density）", KEY_SPEC),
        _f("pe_cycles", "编程/擦除寿命（Program/Erase Cycle / Endurance）", KEY_SPEC, aliases=("pe_cycle",)),
        _f("retention", "数据保持（Data Retention）", KEY_SPEC),
        _f("ecc_requirement", "ECC 要求（ECC Requirement）", KEY_SPEC, aliases=("ecc_capability",)),
        _f("bad_block_requirement", "坏块要求（Bad Block Requirement）", KEY_SPEC, aliases=("factory_bad_block",)),
        _f("page_size", "页大小（Page Size）", KEY_SPEC),
        _f("block_size", "擦除块大小（Erase Block Size）", KEY_SPEC),
        _f("pages_per_block", "每块页数（Pages per Block）", KEY_SPEC),
        _f("program_time", "编程时间（Program Time）", KEY_SPEC),
        _f("erase_time", "擦除时间（Erase Time）", KEY_SPEC),
        _f("operating_temperature", "工作温度范围（Operating Temperature Range）", KEY_SPEC),
        _f("erase_count_observability", "擦写次数可观测能力（Erase Count Observability）", KEY_DIAGNOSTIC, aliases=("lifetime_counter",)),
        _f("ecc_observability", "ECC 纠正 / 不可纠正错误可观测能力（Corrected / Uncorrectable ECC Observability）", KEY_DIAGNOSTIC, aliases=("ecc_status", "ecc_capability")),
        _f("bad_block_observability", "坏块数量可观测能力（Bad Block Count Observability）", KEY_DIAGNOSTIC, aliases=("runtime_bad_block",)),
        _f("wear_distribution_observability", "磨损分布可观测能力（Wear Distribution Observability）", KEY_DIAGNOSTIC),
        _f("failure_status", "读 / 编程 / 擦除失败状态（Read / Program / Erase Failure Status）", KEY_DIAGNOSTIC, "SHOULD", aliases=("read_retry", "program_fail", "erase_fail")),
        _f("data_source_read_method", "数据源与读取方法（Data Source / Read Method）", KEY_DIAGNOSTIC, aliases=("status_register",)),
    ],
    "NOR Flash": [
        _f("capacity", "容量（Capacity）", KEY_SPEC),
        _f("pe_cycles", "编程/擦除寿命（Program/Erase Endurance）", KEY_SPEC),
        _f("retention", "数据保持（Data Retention）", KEY_SPEC),
        _f("page_size", "页编程粒度（Page / Program Granularity）", KEY_SPEC),
        _f("erase_granularity", "扇区 / 块擦除粒度（Sector / Block Erase Granularity）", KEY_SPEC),
        _f("program_time", "编程时间（Program Time）", KEY_SPEC),
        _f("erase_time", "擦除时间（Erase Time）", KEY_SPEC),
        _f("operating_temperature", "工作温度范围（Operating Temperature Range）", KEY_SPEC),
        _f("interface", "接口 / 协议（Interface / Protocol）", KEY_SPEC, "SHOULD"),
        _f("voltage", "供电电压范围（Supply Voltage Range）", KEY_SPEC, "SHOULD"),
        _f("status_register", "状态寄存器 / 操作状态读取（Status Register / Operation Status）", KEY_DIAGNOSTIC),
        _f("program_erase_failure", "编程 / 擦除失败指示（Program / Erase Failure Indication）", KEY_DIAGNOSTIC, "SHOULD", aliases=("program_fail", "erase_fail")),
        _f("ecc_status", "ECC 状态（ECC Status）", KEY_DIAGNOSTIC, "SHOULD"),
        _f("protection_error_status", "保护 / 错误状态（Protection / Error Status）", KEY_DIAGNOSTIC, "SHOULD", aliases=("protection", "error_flag")),
        _f("erase_write_count_observability", "擦写次数是否可直接观测（Erase / Write Count Observability）", KEY_DIAGNOSTIC, aliases=("lifetime_counter",)),
        _f("data_source_read_method", "数据源与读取方法（Data Source / Read Method）", KEY_DIAGNOSTIC, aliases=("status_register",)),
    ],
}


def normalize_type(device_type: str) -> str:
    marker = str(device_type or "").strip().casefold()
    if marker in {"raw nand", "nand", "nand flash", "spi nand", "serial nand", "parallel nand"}:
        return "NAND Flash"
    if marker in {"ssd", "nvme", "nvme ssd", "solid state drive"}:
        return "SSD"
    if marker in {"nor", "nor flash", "spi nor", "serial nor"}:
        return "NOR Flash"
    if marker in {"emmc", "embedded multimediacard"}:
        return "eMMC"
    return str(device_type or "")


def product_fields(device_type: str, extracted_fields: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return fixed key groups plus all remaining extracted fields as comprehensive facts."""
    dtype = normalize_type(device_type)
    result = [dict(x) for x in BASELINES.get(dtype, [])]
    covered = {alias for item in result for alias in item.get("aliases", [])}
    for raw in extracted_fields or []:
        key = str(raw.get("canonical_name") or "")
        if not key or key in covered:
            continue
        item = dict(raw)
        item.update({
            "group": COMPREHENSIVE,
            "group_label": GROUP_LABELS[COMPREHENSIVE],
            "requirement_level": "FULL",
            "aliases": [key],
        })
        result.append(item)
        covered.add(key)
    return result
