"""Explicit synthetic demo seed for Hardware Case search preview.

Never runs automatically. It writes only when --execute-demo is supplied and the
normal Hardware persistent data root resolves to an existing installation.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_data_root import HardwareDataRootResolver

ROOT = Path(__file__).resolve().parents[1]

CASES = (
    {
        "case_id": "DEMO-HW-MCU-RESET-001",
        "title": "MCU 偶发复位",
        "symptom": "MCU 偶发复位，重新上电恢复",
        "root_cause": "RESET_N 受到瞬态干扰",
        "actions": "检查复位信号完整性并增加抗干扰设计",
        "node_id": "DEMO-CF-MCU",
        "node_path": ["控制", "MCU", "复位"],
    },
    {
        "case_id": "DEMO-HW-CAN-INT-001",
        "title": "CAN 通信偶发中断",
        "symptom": "CAN 通信偶发中断",
        "root_cause": "通信链路受到瞬态干扰",
        "actions": "检查终端匹配、布线与干扰耦合路径",
        "node_id": "DEMO-CF-CAN",
        "node_path": ["通信", "CAN"],
    },
    {
        "case_id": "DEMO-HW-LDO-OSC-001",
        "title": "LDO 输出振荡",
        "symptom": "LDO 输出振荡",
        "root_cause": "输出网络稳定性条件不满足",
        "actions": "核对输出电容、ESR 与环路稳定性要求",
        "node_id": "DEMO-CF-LDO",
        "node_path": ["电源", "LDO", "稳定性"],
    },
)


def _field(value: str) -> dict[str, object]:
    return {
        "candidate_value": value,
        "confirmed_value": value,
        "review_disposition": "CONFIRMED",
        "evidence_refs": [],
    }


def seed(service: HardwareCaseBackendService) -> dict[str, object]:
    created = 0
    skipped = 0
    for item in CASES:
        if service.repository.get_case(item["case_id"]) is not None:
            skipped += 1
            continue
        service.create_case(
            {
                "case_id": item["case_id"],
                "title": item["title"],
                "case_status": "PUBLISHED",
                "processing_status": "READY",
                "source_refs": ["synthetic:hardware-retrieval-demo"],
                "product_context": {"product": "SYNTHETIC_DEMO"},
                "facts": {
                    "symptom": _field(item["symptom"]),
                    "root_cause": _field(item["root_cause"]),
                    "actions": _field(item["actions"]),
                },
            }
        )
        service.save_tree_node(
            {
                "node_id": item["node_id"],
                "tree_type": "CIRCUIT_FEATURE",
                "name": item["node_path"][-1],
                "parent_id": None,
                "path": list(item["node_path"]),
                "description": "SYNTHETIC DEMO ONLY",
                "source_ref": "synthetic:hardware-retrieval-demo",
                "active": True,
            }
        )
        service.set_mapping(
            {
                "mapping_id": "MAP-" + item["case_id"],
                "case_id": item["case_id"],
                "tree_type": "CIRCUIT_FEATURE",
                "node_id": item["node_id"],
                "relation_role": "PRIMARY",
                "mapping_status": "CONFIRMED",
                "confidence": 1.0,
                "basis_refs": [],
            }
        )
        created += 1
    return {
        "status": "PASS",
        "synthetic": True,
        "created": created,
        "skipped_existing": skipped,
        "case_ids": [item["case_id"] for item in CASES],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-demo", action="store_true")
    args = parser.parse_args()
    if not args.execute_demo:
        print("DEMO_WRITE_NOT_AUTHORIZED")
        return 2

    resolution = HardwareDataRootResolver(ROOT).resolve()
    if resolution.classification != "EXISTING_INSTALL" or resolution.data_root is None:
        print("RESULT=BLOCKED")
        print("BLOCKER=EXISTING_HARDWARE_INSTALL_REQUIRED")
        print("DATA_ROOT_CLASSIFICATION=" + resolution.classification)
        return 3

    db_path = resolution.data_root / "db" / "hardware_case_mvp.db"
    service = HardwareCaseBackendService(
        HardwareCaseRepository(db_path, initialize_schema=False)
    )
    result = seed(service)
    for key, value in result.items():
        print(f"{key.upper()}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
