from __future__ import annotations

from pathlib import Path

from services.hardware_case_r1_preview_store import HardwareR1PreviewStore


def snapshot(source_id: str = "a" * 64) -> dict:
    return {
        "snapshot_version": "hardware-document-snapshot/v1",
        "source": {"source_id": source_id, "file_name": "A0152-demo.docx"},
        "identity": {
            "source_id": source_id,
            "business_case_id": "A0152",
            "raw_title": "CPU_串口输出配置",
            "identity_status": "PARSED",
            "warnings": [],
        },
        "structure": {"blocks": [{"block_id": "B1", "block_type": "PARAGRAPH", "text": "MCU"}]},
    }


def result(run_id: str = "run-1") -> dict:
    return {
        "status": "PASS",
        "runtime": {"run_id": run_id},
        "knowledge_object": {
            "contract_version": "hardware-case-knowledge-object/v1",
            "review": {"object_status": "CANDIDATE"},
        },
    }


def test_preview_store_survives_new_store_instance(tmp_path: Path):
    db = tmp_path / "preview.db"
    first = HardwareR1PreviewStore(db)
    saved = first.save(snapshot(), result())
    assert saved["preview_id"] == 1

    reopened = HardwareR1PreviewStore(db)
    latest = reopened.latest()
    assert latest is not None
    assert latest["preview_id"] == 1
    assert latest["source_id"] == "a" * 64
    assert latest["snapshot"]["identity"]["business_case_id"] == "A0152"
    assert latest["result"]["knowledge_object"]["review"]["object_status"] == "CANDIDATE"


def test_preview_store_supports_source_run_and_history_lookup(tmp_path: Path):
    store = HardwareR1PreviewStore(tmp_path / "preview.db")
    store.save(snapshot("a" * 64), result("run-a"))
    store.save(snapshot("b" * 64), result("run-b"))

    assert store.latest(source_id="a" * 64)["runtime_run_id"] == "run-a"
    assert store.by_run_id("run-b")["source_id"] == "b" * 64
    assert store.by_id(1)["runtime_run_id"] == "run-a"
    assert [item["preview_id"] for item in store.list(limit=10)] == [2, 1]
