from __future__ import annotations

from pathlib import Path
import sqlite3

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

def test_preview_store_delete_one_only_removes_target_row(tmp_path: Path):
    store = HardwareR1PreviewStore(tmp_path / "preview.db")
    first = store.save(snapshot("a" * 64), result("run-a"))
    second = store.save(snapshot("b" * 64), result("run-b"))

    assert store.delete(first["preview_id"]) is True
    assert store.by_id(first["preview_id"]) is None
    assert store.by_id(second["preview_id"]) is not None
    assert [item["preview_id"] for item in store.list(limit=10)] == [
        second["preview_id"]
    ]


def test_preview_store_clear_is_idempotent_and_scoped_to_preview_table(tmp_path: Path):
    db = tmp_path / "preview.db"
    store = HardwareR1PreviewStore(db)
    store.save(snapshot("a" * 64), result("run-a"))
    store.save(snapshot("b" * 64), result("run-b"))

    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE unrelated_sentinel (record_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO unrelated_sentinel(record_id) VALUES ('keep-me')"
        )

    assert store.clear() == 2
    assert store.list(limit=10) == []
    assert store.clear() == 0

    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT record_id FROM unrelated_sentinel"
        ).fetchone()
    assert row == ("keep-me",)

