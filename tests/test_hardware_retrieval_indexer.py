from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from services.hardware_retrieval_indexer import (
    HardwareRetrievalGenerationIndexer,
    HardwareRetrievalIndexerError,
    HardwareRetrievalMetadataStore,
)
from services.hardware_retrieval_metadata import build_retrieval_metadata


def projection(knowledge_id: str, case_id: str, hash_char: str):
    return {
        "projection_schema_version": 1,
        "knowledge_id": knowledge_id,
        "public_ref": f"HW-PUBLIC-{case_id}-R1",
        "business_case_id": case_id,
        "title": f"{case_id} MCU reset",
        "symptom": "MCU 偶发复位",
        "occurrence_condition": None,
        "failure_mode": "reset",
        "root_cause": "RESET_N disturbance",
        "failure_mechanism": None,
        "analysis_process": None,
        "actions": "improve reset filtering",
        "verification_result": None,
        "engineering_rule": None,
        "design_constraint": None,
        "diagnostic_clue": None,
        "verification_method": None,
        "applicability": None,
        "conclusion": None,
        "interface": "CAN",
        "signal": "RESET_N",
        "key_parameters": [],
        "device_refs": [
            {
                "category": "MCU",
                "generic_name_or_series": "STM32 MCU",
                "internal_material_no": None,
                "manufacturer": None,
                "manufacturer_part_no": None,
                "evidence_refs": ["EV-1"],
                "status": "EXPLICIT",
            }
        ],
        "evidence_refs": ["EV-1"],
        "source_domain": "HARDWARE_CASE",
        "source_object_type": "HARDWARE_CASE",
        "formal_revision": 1,
        "formal_status": "ACTIVE",
        "formal_object_hash": hash_char * 64,
        "projected_at": "2026-10-07T00:00:00+00:00",
    }


def item(knowledge_id: str, case_id: str, hash_char: str):
    source = projection(knowledge_id, case_id, hash_char)
    metadata = build_retrieval_metadata(
        source,
        [{
            "term": "偶发复位",
            "kind": "FACT",
            "source_term": "偶发复位",
            "source_fields": ["symptom"],
        }],
    )
    return {"projection": source, "metadata": metadata}


class StubAdapter:
    def __init__(self):
        self.ensure_calls = []
        self.upsert_calls = []
        self.alias_calls = []
        self.fail_upsert_id = None
        self.fail_alias = False
        self.existing_indexes = set()

    def ensure_index(self, index_name, mapping):
        self.ensure_calls.append((index_name, mapping))
        if index_name in self.existing_indexes:
            return {"index": index_name, "created": False}
        self.existing_indexes.add(index_name)
        return {"index": index_name, "created": True}

    def upsert(self, index_name, document_id, document, *, refresh=True):
        self.upsert_calls.append((index_name, document_id, document, refresh))
        if document_id == self.fail_upsert_id:
            raise RuntimeError("synthetic upsert failure")
        return {"index": index_name, "document_id": document_id, "result": "created"}

    def switch_alias(self, alias, new_index):
        self.alias_calls.append((alias, new_index))
        if self.fail_alias:
            raise RuntimeError("synthetic alias failure")
        return {"alias": alias, "index": new_index, "acknowledged": True}


def test_metadata_store_generation_journal_and_hash(tmp_path: Path):
    store = HardwareRetrievalMetadataStore(tmp_path / "metadata.db")
    value = item("KO-1", "A1001", "a")
    store.begin_generation(
        generation_id="g1",
        index_name="hardware-knowledge-search-v1-g1",
        records=[{
            "knowledge_id": "KO-1",
            "business_case_id": "A1001",
            "formal_object_hash": "a" * 64,
            "metadata": value["metadata"],
        }],
    )

    generation = store.get_generation("g1")
    rows = store.list_metadata("g1")
    assert generation["status"] == "BUILDING"
    assert generation["expected_count"] == 1
    assert len(rows) == 1
    assert len(rows[0]["metadata_hash"]) == 64
    assert store.active_generation() is None


def test_full_rebuild_switches_alias_only_after_all_documents(tmp_path: Path):
    store = HardwareRetrievalMetadataStore(tmp_path / "metadata.db")
    adapter = StubAdapter()
    indexer = HardwareRetrievalGenerationIndexer(adapter, store)
    inputs = [
        item("KO-1", "A1001", "a"),
        item("KO-2", "A1002", "b"),
    ]

    result = indexer.rebuild_all("g1", inputs)

    assert result["indexed_count"] == 2
    assert [call[1] for call in adapter.upsert_calls] == ["KO-1", "KO-2"]
    assert adapter.alias_calls == [
        ("hardware-knowledge-search-active", "hardware-knowledge-search-v1-g1")
    ]
    assert store.active_generation()["generation_id"] == "g1"
    assert store.get_generation("g1")["status"] == "ACTIVE"


def test_partial_upsert_failure_preserves_previous_active_generation(tmp_path: Path):
    store = HardwareRetrievalMetadataStore(tmp_path / "metadata.db")
    adapter = StubAdapter()
    indexer = HardwareRetrievalGenerationIndexer(adapter, store)
    indexer.rebuild_all("g0", [item("KO-0", "A1000", "0")])

    adapter.fail_upsert_id = "KO-2"
    with pytest.raises(HardwareRetrievalIndexerError):
        indexer.rebuild_all(
            "g1",
            [item("KO-1", "A1001", "a"), item("KO-2", "A1002", "b")],
        )

    assert store.active_generation()["generation_id"] == "g0"
    assert store.get_generation("g1")["status"] == "FAILED"
    assert adapter.alias_calls == [
        ("hardware-knowledge-search-active", "hardware-knowledge-search-v1-g0")
    ]


def test_alias_failure_preserves_previous_active_generation(tmp_path: Path):
    store = HardwareRetrievalMetadataStore(tmp_path / "metadata.db")
    adapter = StubAdapter()
    indexer = HardwareRetrievalGenerationIndexer(adapter, store)
    indexer.rebuild_all("g0", [item("KO-0", "A1000", "0")])

    adapter.fail_alias = True
    with pytest.raises(HardwareRetrievalIndexerError):
        indexer.rebuild_all("g1", [item("KO-1", "A1001", "a")])

    assert store.active_generation()["generation_id"] == "g0"
    assert store.get_generation("g1")["status"] == "FAILED"


def test_duplicate_knowledge_id_fails_before_generation_creation(tmp_path: Path):
    store = HardwareRetrievalMetadataStore(tmp_path / "metadata.db")
    adapter = StubAdapter()
    indexer = HardwareRetrievalGenerationIndexer(adapter, store)
    duplicate = item("KO-1", "A1001", "a")

    with pytest.raises(HardwareRetrievalIndexerError) as error:
        indexer.rebuild_all("g1", [duplicate, deepcopy(duplicate)])

    assert error.value.code == "GENERATION_KNOWLEDGE_ID_INVALID"
    with pytest.raises(HardwareRetrievalIndexerError):
        store.get_generation("g1")
    assert adapter.ensure_calls == []


def test_existing_generation_index_fails_without_alias_switch(tmp_path: Path):
    store = HardwareRetrievalMetadataStore(tmp_path / "metadata.db")
    adapter = StubAdapter()
    adapter.existing_indexes.add("hardware-knowledge-search-v1-g1")
    indexer = HardwareRetrievalGenerationIndexer(adapter, store)

    with pytest.raises(HardwareRetrievalIndexerError) as error:
        indexer.rebuild_all("g1", [item("KO-1", "A1001", "a")])

    assert error.value.code == "INDEX_GENERATION_ALREADY_EXISTS"
    assert store.get_generation("g1")["status"] == "FAILED"
    assert adapter.alias_calls == []


def test_rebuild_does_not_mutate_formal_projection_input(tmp_path: Path):
    store = HardwareRetrievalMetadataStore(tmp_path / "metadata.db")
    adapter = StubAdapter()
    indexer = HardwareRetrievalGenerationIndexer(adapter, store)
    value = item("KO-1", "A1001", "a")
    before = deepcopy(value)

    indexer.rebuild_all("g1", [value])

    assert value == before
