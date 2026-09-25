"""Storage-owned long-content Domain Strategy for Unified Agent Runtime.

S-A03 freezes business semantics only:
- Source identity and the 37-field Coverage Universe;
- LogicalUnit / AtomicGroup definitions for eMMC Parameter extraction;
- deterministic Business Merge / Dedup / Conflict / Ordering;
- Storage Business Completeness / Review Gate.

Runtime remains responsible for planning/execution, retry, checkpoint, partial commit,
coverage accounting and resume.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable


EMMC_FIELD_ORDER = (
    # Document identity (8)
    "manufacturer",
    "product_family",
    "covered_part_numbers",
    "document_number",
    "revision",
    "revision_date",
    "capacity",
    "emmc_version",
    # Media / partition / lifetime (12)
    "native_nand_type",
    "partition_storage_mode",
    "boot_area_1",
    "boot_area_2",
    "rpmb",
    "general_purpose_partition",
    "enhanced_user_data_area",
    "default_user_data_area",
    "enhanced_area_supported",
    "pe_cycle",
    "data_retention",
    "endurance_condition",
    # Standard health (5)
    "device_life_time_est_typ_a",
    "device_life_time_est_typ_b",
    "pre_eol_info",
    "bkops_status",
    "vendor_proprietary_health_report",
    # Vendor health (5)
    "vendor_health_monitoring",
    "access_method",
    "bad_block_count",
    "erase_cycle_count",
    "erase_cycle_granularity",
    # Management / reliability (7)
    "reliable_write",
    "bkops",
    "cache",
    "sanitize",
    "power_off_notification",
    "error_reporting",
    "field_firmware_update",
)

EMMC_ATOMIC_GROUPS = (
    (
        "document_identity",
        (
            "manufacturer",
            "product_family",
            "covered_part_numbers",
            "document_number",
            "revision",
            "revision_date",
            "capacity",
            "emmc_version",
        ),
    ),
    (
        "media_endurance",
        (
            "native_nand_type",
            "pe_cycle",
            "data_retention",
            "endurance_condition",
        ),
    ),
    (
        "partition_modes",
        (
            "partition_storage_mode",
            "boot_area_1",
            "boot_area_2",
            "rpmb",
            "general_purpose_partition",
            "enhanced_user_data_area",
            "default_user_data_area",
            "enhanced_area_supported",
        ),
    ),
    (
        "standard_health",
        (
            "device_life_time_est_typ_a",
            "device_life_time_est_typ_b",
            "pre_eol_info",
            "bkops_status",
            "vendor_proprietary_health_report",
        ),
    ),
    (
        "vendor_health",
        (
            "vendor_health_monitoring",
            "access_method",
            "bad_block_count",
            "erase_cycle_count",
            "erase_cycle_granularity",
        ),
    ),
    (
        "management_reliability",
        (
            "reliable_write",
            "bkops",
            "cache",
            "sanitize",
            "power_off_notification",
            "error_reporting",
            "field_firmware_update",
        ),
    ),
)



EMMC_IDENTITY_FIELDS = EMMC_FIELD_ORDER[:8]
EMMC_ANALYSIS_FIELDS = EMMC_FIELD_ORDER[8:]

EMMC_FIELD_LABELS = {
    "manufacturer": "厂家（Manufacturer）",
    "product_family": "产品族（Product Family）",
    "covered_part_numbers": "覆盖料号（Covered Part Numbers）",
    "document_number": "文档编号（Document Number）",
    "revision": "版本（Revision）",
    "revision_date": "版本日期（Revision Date）",
    "capacity": "容量（Capacity）",
    "emmc_version": "eMMC 版本（eMMC Version）",
    "native_nand_type": "原生 NAND 类型（Native NAND Type）",
    "partition_storage_mode": "分区存储模式（Partition Storage Mode）",
    "boot_area_1": "Boot Area 1",
    "boot_area_2": "Boot Area 2",
    "rpmb": "RPMB",
    "general_purpose_partition": "通用分区（General Purpose Partition）",
    "enhanced_user_data_area": "增强用户数据区（Enhanced User Data Area）",
    "default_user_data_area": "默认用户数据区（Default User Data Area）",
    "enhanced_area_supported": "增强区支持（Enhanced Area Supported）",
    "pe_cycle": "擦写次数（P/E Cycle）",
    "data_retention": "数据保持（Data Retention）",
    "endurance_condition": "寿命条件（Endurance Condition）",
    "device_life_time_est_typ_a": "设备寿命估算 A（EXT_CSD[268]）",
    "device_life_time_est_typ_b": "设备寿命估算 B（EXT_CSD[269]）",
    "pre_eol_info": "寿命预警（PRE_EOL_INFO / EXT_CSD[267]）",
    "bkops_status": "后台操作状态（BKOPS_STATUS / EXT_CSD[246]）",
    "vendor_proprietary_health_report": "厂商专有健康报告（EXT_CSD[301:270]）",
    "vendor_health_monitoring": "厂商增强健康监测（Vendor Health Monitoring）",
    "access_method": "访问方式（Access Method）",
    "bad_block_count": "坏块计数能力（Bad Block Count）",
    "erase_cycle_count": "擦除次数计数能力（Erase Cycle Count）",
    "erase_cycle_granularity": "擦除次数粒度（Erase Cycle Granularity）",
    "reliable_write": "可靠写（Reliable Write）",
    "bkops": "后台操作（BKOPS）",
    "cache": "缓存（Cache）",
    "sanitize": "安全擦除（Sanitize）",
    "power_off_notification": "掉电通知（Power Off Notification）",
    "error_reporting": "错误报告（Error Reporting）",
    "field_firmware_update": "现场固件升级（Field Firmware Update）",
}

EMMC_DIAGNOSTIC_FIELDS = {
    "device_life_time_est_typ_a", "device_life_time_est_typ_b", "pre_eol_info",
    "bkops_status", "vendor_proprietary_health_report", "vendor_health_monitoring",
    "access_method", "bad_block_count", "erase_cycle_count", "erase_cycle_granularity",
}

def emmc_knowledge_type(field_key: str) -> str:
    if field_key in EMMC_DIAGNOSTIC_FIELDS:
        return "diagnostic_capability"
    if field_key in {
        "reliable_write", "bkops", "cache", "sanitize",
        "power_off_notification", "error_reporting", "field_firmware_update",
    }:
        return "device_requirement"
    return "specification"

VALID_STATUSES = {"found", "missing", "ambiguous", "conflict"}


def _stable_hash(value: Any) -> str:
    data = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _fact_key(raw: dict[str, Any]) -> str:
    return str(raw.get("field_key") or raw.get("field_id") or raw.get("name") or "").strip()


def _normalize_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _canonical_fact(raw: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(raw)
    key = _fact_key(item)
    item["field_key"] = key
    item.pop("field_id", None)
    item.pop("name", None)
    item["status"] = _normalize_status(item.get("status"))
    evidence = item.get("evidence")
    if evidence is None:
        item["evidence"] = []
    elif isinstance(evidence, dict):
        item["evidence"] = [evidence]
    elif not isinstance(evidence, list):
        item["evidence"] = []
    return item


def _semantic_projection(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": _normalize_status(item.get("status")),
        "value": item.get("value", item.get("normalized_value")),
        "unit": item.get("unit"),
        "condition": item.get("condition"),
        "scope_type": item.get("scope_type"),
        "scope_values": item.get("scope_values") or [],
        "derived": bool(item.get("derived", False)),
        "knowledge_type": item.get("knowledge_type"),
    }


def _evidence_identity(value: dict[str, Any]) -> str:
    return _stable_hash(
        {
            "source_id": value.get("source_id") or (value.get("source") or {}).get("source_id"),
            "page": value.get("page"),
            "section": value.get("section"),
            "quote": value.get("quote") or value.get("excerpt"),
            "locator": value.get("locator"),
        }
    )


def _merge_evidence(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*left, *right]:
        if not isinstance(item, dict):
            continue
        marker = _evidence_identity(item)
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(copy.deepcopy(item))
    return merged


class StorageEmmcDomainStrategy:
    """Business-owned eMMC field-group projection and merge semantics."""

    strategy_ref = "storage_emmc_field_groups@1"
    field_order = EMMC_FIELD_ORDER
    atomic_groups = EMMC_ATOMIC_GROUPS

    @classmethod
    def descriptor(
        cls,
        *,
        source_ref: dict[str, Any],
        source_text: str,
        schema_version: str = "emmc_schema_v0.2",
        partition_key: str | None = None,
    ) -> dict[str, Any]:
        """Pure-Python descriptor used by tests and the Runtime projector."""
        source_id = str(source_ref.get("source_id") or "").strip()
        fingerprint = str(source_ref.get("fingerprint") or "").strip()
        if not source_id or not fingerprint:
            raise ValueError("source_ref requires source_id and fingerprint")
        if not isinstance(source_text, str) or not source_text.strip():
            raise ValueError("source_text must contain the frozen structured source")

        field_to_group: dict[str, str] = {}
        for group_id, fields in cls.atomic_groups:
            for field in fields:
                if field in field_to_group:
                    raise ValueError(f"field belongs to multiple atomic groups: {field}")
                field_to_group[field] = group_id
        if set(field_to_group) != set(cls.field_order):
            raise ValueError("atomic groups must cover exactly the 37-field universe")

        units = [
            {
                "unit_id": f"field:{field}",
                "source_id": source_id,
                "locator": {"type": "TARGET_FIELD", "field_key": field},
                "inline_payload": {"field_key": field, "schema_version": schema_version},
                "group_id": field_to_group[field],
                "context_refs": ["storage_source_text", "storage_schema_context"],
                "partition_key": partition_key,
                "metadata": {
                    "business_domain": "STORAGE",
                    "device_type": "emmc",
                    "storage_field_key": field,
                },
            }
            for field in cls.field_order
        ]
        groups = [
            {
                "group_id": f"emmc:{group_id}",
                "unit_ids": [f"field:{field}" for field in fields],
                "policy": "KEEP_TOGETHER",
                "grouping_hint": "storage_emmc_linked_fields",
                "metadata": {"field_keys": list(fields)},
            }
            for group_id, fields in cls.atomic_groups
        ]
        universe = {
            "coverage_type": "ITEM",
            "required_units": [f"field:{field}" for field in cls.field_order],
            "universe_fingerprint": _stable_hash(
                {
                    "source_fingerprint": fingerprint,
                    "schema_version": schema_version,
                    "required_fields": cls.field_order,
                }
            ),
            "partition_key": partition_key,
        }
        return {
            "bundle_id": f"storage:emmc:{fingerprint}:{schema_version}",
            "source_ref": copy.deepcopy(source_ref),
            "logical_units": units,
            "atomic_groups": groups,
            "shared_context": {
                "storage_source_text": source_text,
                "storage_schema_context": {
                    "device_type": "emmc",
                    "schema_version": schema_version,
                    "required_field_order": list(cls.field_order),
                    "status_values": sorted(VALID_STATUSES),
                    "missing_is_valid": True,
                    "do_not_infer_unspecified_facts": True,
                },
            },
            "default_partition_key": partition_key,
            "coverage_universe": universe,
            "strategy_ref": cls.strategy_ref,
        }

    @classmethod
    def to_runtime_bundle(cls, descriptor: dict[str, Any]):
        """Convert the frozen descriptor into Runtime SourceBundle when Runtime is installed."""
        from runtime.contracts import (  # type: ignore
            AtomicGroup,
            AtomicGroupPolicy,
            ContentSource,
            LogicalUnit,
            SourceBundle,
            SourceRef,
        )

        src = SourceRef.model_validate(descriptor["source_ref"])
        groups = [
            AtomicGroup(
                group_id=item["group_id"],
                unit_ids=item["unit_ids"],
                policy=AtomicGroupPolicy(item["policy"]),
                grouping_hint=item.get("grouping_hint"),
                metadata=item.get("metadata") or {},
            )
            for item in descriptor["atomic_groups"]
        ]
        units = [LogicalUnit.model_validate(item) for item in descriptor["logical_units"]]
        return SourceBundle(
            bundle_id=descriptor["bundle_id"],
            sources=[
                ContentSource(
                    source=src,
                    inline_content=descriptor["shared_context"]["storage_source_text"],
                    partition_key=descriptor.get("default_partition_key"),
                    metadata={"business_domain": "STORAGE", "device_type": "emmc"},
                )
            ],
            logical_units=units,
            atomic_groups=groups,
            shared_context=descriptor["shared_context"],
            default_partition_key=descriptor.get("default_partition_key"),
            metadata={
                "business_domain": "STORAGE",
                "device_type": "emmc",
                "strategy_ref": descriptor["strategy_ref"],
            },
        )

    @classmethod
    def to_runtime_coverage_universe(cls, descriptor: dict[str, Any]):
        from runtime.contracts import CoverageUnit, CoverageUniverse, SourceRef  # type: ignore

        src = SourceRef.model_validate(descriptor["source_ref"])
        universe = descriptor["coverage_universe"]
        return CoverageUniverse(
            source=src,
            coverage_type="ITEM",
            unit_targets=[
                CoverageUnit(
                    unit_id=unit_id,
                    locator={"field_key": unit_id.split(":", 1)[1]},
                    required=True,
                    metadata={"business_domain": "STORAGE"},
                )
                for unit_id in universe["required_units"]
            ],
            universe_fingerprint=universe["universe_fingerprint"],
            partition_key=universe.get("partition_key"),
            metadata={"schema_version": descriptor["shared_context"]["storage_schema_context"]["schema_version"]},
        )

    @classmethod
    def merge_partials(cls, partials: Iterable[Any]) -> dict[str, Any]:
        """Deterministic Storage Business Merger.

        No field is fabricated to fill coverage.  Missing coverage remains a technical
        incomplete condition.  Semantic disagreement is preserved as an explicit merge
        conflict and must enter the Review Gate.
        """
        by_field: dict[str, dict[str, Any]] = {}
        merge_conflicts: list[dict[str, Any]] = []
        source_partial_count = 0

        for partial in partials:
            source_partial_count += 1
            data = getattr(partial, "data", partial)
            if not isinstance(data, dict):
                merge_conflicts.append({"type": "invalid_partial", "partial_index": source_partial_count - 1})
                continue
            raw_fields = data.get("fields")
            if raw_fields is None and _fact_key(data):
                raw_fields = [data]
            if not isinstance(raw_fields, list):
                merge_conflicts.append({"type": "invalid_partial_fields", "partial_index": source_partial_count - 1})
                continue

            for raw in raw_fields:
                if not isinstance(raw, dict):
                    merge_conflicts.append({"type": "invalid_fact", "partial_index": source_partial_count - 1})
                    continue
                fact = _canonical_fact(raw)
                key = fact["field_key"]
                if key not in cls.field_order:
                    merge_conflicts.append({"type": "unknown_field", "field_key": key})
                    continue
                current = by_field.get(key)
                if current is None:
                    by_field[key] = fact
                    continue
                if _semantic_projection(current) == _semantic_projection(fact):
                    current["evidence"] = _merge_evidence(current.get("evidence", []), fact.get("evidence", []))
                    continue

                # Never silently prefer found/missing or one source over another.
                conflict = {
                    "type": "semantic_conflict",
                    "field_key": key,
                    "left": copy.deepcopy(current),
                    "right": copy.deepcopy(fact),
                }
                merge_conflicts.append(conflict)
                combined_evidence = _merge_evidence(current.get("evidence", []), fact.get("evidence", []))
                by_field[key] = {
                    "field_key": key,
                    "value": None,
                    "unit": None,
                    "condition": None,
                    "scope_type": None,
                    "scope_values": [],
                    "evidence": combined_evidence,
                    "confidence": None,
                    "status": "conflict",
                    "derived": False,
                    "knowledge_type": current.get("knowledge_type") or fact.get("knowledge_type"),
                    "merge_conflict": conflict,
                }

        fields = [by_field[key] for key in cls.field_order if key in by_field]
        missing_field_keys = [key for key in cls.field_order if key not in by_field]
        complete = not missing_field_keys and not any(c["type"].startswith("invalid_") for c in merge_conflicts)
        return {
            "fields": fields,
            "field_count": len(fields),
            "missing_field_keys": missing_field_keys,
            "merge_conflicts": merge_conflicts,
            "complete": complete,
            "source_partial_count": source_partial_count,
        }

    @classmethod
    def business_gate(
        cls,
        merged: dict[str, Any],
        *,
        evidence_resolved: bool = True,
        review_resolved: bool = True,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        fields = merged.get("fields") or []
        by_key = {_fact_key(item): item for item in fields if isinstance(item, dict)}

        missing_keys = [key for key in cls.field_order if key not in by_key]
        if missing_keys:
            reasons.append("FIELD_COVERAGE_INCOMPLETE")

        invalid_status = [
            key for key, item in by_key.items() if _normalize_status(item.get("status")) not in VALID_STATUSES
        ]
        if invalid_status:
            reasons.append("INVALID_FIELD_STATUS")

        review_statuses = [
            key for key, item in by_key.items() if _normalize_status(item.get("status")) in {"ambiguous", "conflict"}
        ]
        if review_statuses and not review_resolved:
            reasons.append("BUSINESS_REVIEW_REQUIRED")

        found_without_evidence = [
            key
            for key, item in by_key.items()
            if _normalize_status(item.get("status")) in {"found", "conflict", "ambiguous"}
            and not (item.get("evidence") or [])
        ]
        if found_without_evidence or not evidence_resolved:
            reasons.append("EVIDENCE_NOT_RESOLVED")

        if merged.get("missing_field_keys"):
            reasons.append("MERGE_INCOMPLETE")
        if any(c.get("type", "").startswith("invalid_") for c in merged.get("merge_conflicts") or []):
            reasons.append("MERGE_STRUCTURAL_ERROR")

        reasons = list(dict.fromkeys(reasons))
        return {
            "passed": not reasons,
            "business_consumable": not reasons,
            "reasons": reasons,
            "field_count": len(by_key),
            "review_field_keys": review_statuses,
            "evidence_missing_field_keys": found_without_evidence,
        }
