from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from services.hardware_search_adapter import (
    HardwareSearchAdapter,
    HardwareSearchAdapterError,
)


INDEX_V1 = "hardware-search-w0-v1"
INDEX_V2 = "hardware-search-w0-v2"
ALIAS = "hardware-knowledge-search-active"

MAPPING: dict[str, Any] = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "knowledge_id": {"type": "keyword"},
            "business_case_id": {"type": "keyword"},
            "title": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "search_text": {"type": "text"},
            "tags": {"type": "text"},
            "component": {"type": "keyword"},
            "interface": {"type": "keyword"},
        },
    },
}

DOCS = [
    {
        "knowledge_id": "KO-SMOKE-1",
        "business_case_id": "SMOKE-A",
        "title": "MCU intermittent reset",
        "search_text": "MCU intermittent reset reboot power cycle recovery 偶发复位 重新上电恢复",
        "tags": ["MCU", "reset", "reboot", "偶发复位"],
        "component": "MCU",
        "interface": "GPIO",
    },
    {
        "knowledge_id": "KO-SMOKE-2",
        "business_case_id": "SMOKE-B",
        "title": "CAN communication interruption",
        "search_text": "CAN communication interruption bus timeout 通信中断",
        "tags": ["CAN", "communication", "interrupt"],
        "component": "CAN_TRANSCEIVER",
        "interface": "CAN",
    },
    {
        "knowledge_id": "KO-SMOKE-3",
        "business_case_id": "SMOKE-C",
        "title": "Operational amplifier batch defect",
        "search_text": "op amp operational amplifier batch defect 批量不良 运放",
        "tags": ["OP_AMP", "batch defect", "运放"],
        "component": "OP_AMP",
        "interface": "ANALOG",
    },
    {
        "knowledge_id": "KO-SMOKE-4",
        "business_case_id": "SMOKE-D",
        "title": "LDO output oscillation",
        "search_text": "LDO output oscillation regulator instability 输出振荡",
        "tags": ["LDO", "oscillation", "振荡"],
        "component": "LDO",
        "interface": "POWER",
    },
]


def _wait_ready(adapter: HardwareSearchAdapter, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: str | None = None
    while time.monotonic() < deadline:
        try:
            status = adapter.health()
            if status["reachable"] and status["distribution"] == "opensearch":
                return status
        except HardwareSearchAdapterError as error:
            last = error.code
        time.sleep(2)
    raise RuntimeError(f"SEARCH_ENGINE_NOT_READY:{last or 'UNKNOWN'}")


def _bootstrap(adapter: HardwareSearchAdapter) -> dict[str, Any]:
    adapter.delete_index(INDEX_V1)
    adapter.delete_index(INDEX_V2)

    adapter.ensure_index(INDEX_V1, MAPPING)
    for doc in DOCS:
        adapter.upsert(INDEX_V1, doc["knowledge_id"], doc)
    adapter.switch_alias(ALIAS, INDEX_V1)

    bm25 = adapter.search(ALIAS, "MCU reset")
    filtered = adapter.search(ALIAS, filters={"component": "OP_AMP"})
    if not bm25["hits"] or bm25["hits"][0]["_id"] != "KO-SMOKE-1":
        raise RuntimeError("BM25_GATE_FAILED")
    if [hit["_id"] for hit in filtered["hits"]] != ["KO-SMOKE-3"]:
        raise RuntimeError("FILTER_GATE_FAILED")

    adapter.ensure_index(INDEX_V2, MAPPING)
    for doc in DOCS:
        adapter.upsert(INDEX_V2, doc["knowledge_id"], doc)
    adapter.switch_alias(ALIAS, INDEX_V2)

    alias_result = adapter.search(ALIAS, "LDO oscillation")
    if not alias_result["hits"] or alias_result["hits"][0]["_id"] != "KO-SMOKE-4":
        raise RuntimeError("ALIAS_GATE_FAILED")

    return {
        "index_create": "PASS",
        "upsert": "PASS",
        "bm25": "PASS",
        "filter": "PASS",
        "alias": "PASS",
        "active_index": INDEX_V2,
    }


def _verify_existing(adapter: HardwareSearchAdapter) -> dict[str, Any]:
    mcu = adapter.search(ALIAS, "MCU reset")
    can = adapter.search(ALIAS, filters={"interface": "CAN"})
    if not mcu["hits"] or mcu["hits"][0]["_id"] != "KO-SMOKE-1":
        raise RuntimeError("RESTART_BM25_GATE_FAILED")
    if [hit["_id"] for hit in can["hits"]] != ["KO-SMOKE-2"]:
        raise RuntimeError("RESTART_FILTER_GATE_FAILED")
    return {
        "restart_persistence": "PASS",
        "bm25_after_restart": "PASS",
        "filter_after_restart": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("bootstrap", "verify-existing"),
        default="bootstrap",
    )
    parser.add_argument("--wait-seconds", type=float, default=120.0)
    args = parser.parse_args()

    adapter = HardwareSearchAdapter(
        os.environ.get("HARDWARE_SEARCH_BASE_URL", "http://127.0.0.1:9200"),
        timeout_seconds=5,
    )
    health = _wait_ready(adapter, args.wait_seconds)
    result = _bootstrap(adapter) if args.phase == "bootstrap" else _verify_existing(adapter)
    print(
        json.dumps(
            {
                "phase": args.phase,
                "health": health,
                **result,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HardwareSearchAdapterError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
