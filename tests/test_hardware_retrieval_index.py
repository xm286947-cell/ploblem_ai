from __future__ import annotations

from copy import deepcopy

import pytest

from services.hardware_retrieval_index import (
    HARDWARE_RETRIEVAL_INDEX_MAPPING,
    HardwareRetrievalIndexError,
    HardwareRetrievalIndexWriter,
    build_index_document,
)
from services.hardware_retrieval_metadata import build_retrieval_metadata


def projection() -> dict:
    return {
        "projection_schema_version": 1,
        "knowledge_id": "KO-HW-W1C-001",
        "public_ref": "HW-PUBLIC-W1C-R1",
        "business_case_id": "A9101",
        "title": "MCU intermittent reset",
        "symptom": "MCU 偶发复位，重新上电恢复",
        "occurrence_condition": "低温启动后偶发",
        "failure_mode": "reset",
        "root_cause": "RESET_N line susceptible to disturbance",
        "failure_mechanism": "noise coupled into reset path",
        "analysis_process": "scope captured RESET_N disturbance",
        "actions": "improve reset filtering",
        "verification_result": "retest passed",
        "engineering_rule": "reset path requires noise margin",
        "design_constraint": "keep reset trace away from aggressors",
        "diagnostic_clue": "power cycle restores operation",
        "verification_method": "cold boot repeated test",
        "applicability": "MCU control board",
        "conclusion": "reset path robustness issue",
        "interface": "CAN",
        "signal": "RESET_N",
        "key_parameters": [{"name": "VDD", "value": "3.3 V", "unit": "V"}],
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
        "formal_object_hash": "d" * 64,
        "projected_at": "2026-10-07T00:00:00+00:00",
    }


def metadata(source=None):
    source = source or projection()
    return build_retrieval_metadata(
        source,
        [
            {
                "term": "偶发复位",
                "kind": "FACT",
                "source_term": "偶发复位",
                "source_fields": ["symptom"],
            },
            {
                "term": "reset-n",
                "kind": "NORMALIZED",
                "source_term": "RESET_N",
                "source_fields": ["signal"],
            },
            {
                "term": "reboot",
                "kind": "EXPANSION",
                "source_term": "偶发复位",
                "source_fields": ["symptom"],
            },
        ],
    )


def test_index_document_separates_rankable_and_recall_only_text():
    result = build_index_document(projection(), metadata())

    assert result["contract_version"] == "hardware-retrieval-index-document/v1"
    assert "偶发复位" in result["search_text"]
    assert "reset-n" in result["search_text"]
    assert "reboot" not in result["search_text"]
    assert "reboot" in result["recall_text"]
    assert result["fact_tags"] == ["偶发复位"]
    assert result["normalized_tags"] == ["reset-n"]
    assert result["expansion_tags"] == ["reboot"]


def test_structured_scope_contains_only_explicit_formal_derived_values():
    result = build_index_document(projection(), metadata())
    assert result["scope"] == {
        "interfaces": ["CAN"],
        "signals": ["RESET_N"],
        "devices": ["MCU", "STM32 MCU"],
    }
    assert "reboot" not in str(result["scope"])


def test_tag_provenance_preserves_claim_boundary():
    result = build_index_document(projection(), metadata())
    expansion = [
        item for item in result["tag_provenance"]
        if item["kind"] == "EXPANSION"
    ][0]
    assert expansion["usage"] == "RECALL_ONLY"
    assert expansion["derived"] is True
    assert expansion["claim_safe"] is False


def test_mutated_metadata_fails_closed():
    source = projection()
    value = metadata(source)
    broken = deepcopy(value)
    broken["tags"][2]["claim_safe"] = True
    with pytest.raises(HardwareRetrievalIndexError) as error:
        build_index_document(source, broken)
    assert error.value.code == "RETRIEVAL_METADATA_MISMATCH"


def test_formal_hash_or_identity_mismatch_fails_closed():
    source = projection()
    value = metadata(source)
    changed = deepcopy(source)
    changed["formal_object_hash"] = "e" * 64
    with pytest.raises(HardwareRetrievalIndexError) as error:
        build_index_document(changed, value)
    assert error.value.code == "RETRIEVAL_METADATA_MISMATCH"


def test_index_mapping_is_strict_and_expansion_is_not_a_filter():
    mappings = HARDWARE_RETRIEVAL_INDEX_MAPPING["mappings"]
    assert mappings["dynamic"] == "strict"
    props = mappings["properties"]
    assert props["scope"]["properties"]["interfaces"]["type"] == "keyword"
    assert props["scope"]["properties"]["devices"]["type"] == "keyword"
    assert props["expansion_tags"]["type"] == "text"
    assert "expansion_tags" not in props["scope"]["properties"]


def test_document_build_is_deterministic():
    source = projection()
    value = metadata(source)
    assert build_index_document(source, value) == build_index_document(source, value)


class StubAdapter:
    def __init__(self):
        self.ensure_calls = []
        self.upsert_calls = []

    def ensure_index(self, index_name, mapping):
        self.ensure_calls.append((index_name, mapping))
        return {"index": index_name, "created": True}

    def upsert(self, index_name, document_id, document, *, refresh=True):
        self.upsert_calls.append((index_name, document_id, document, refresh))
        return {
            "index": index_name,
            "document_id": document_id,
            "result": "created",
        }


def test_writer_reuses_w0_adapter_contract():
    adapter = StubAdapter()
    writer = HardwareRetrievalIndexWriter(
        adapter,
        index_name="hardware-retrieval-w1c-v1",
    )
    result = writer.write(projection(), metadata())

    assert result["knowledge_id"] == "KO-HW-W1C-001"
    assert adapter.ensure_calls[0][0] == "hardware-retrieval-w1c-v1"
    assert adapter.ensure_calls[0][1] == HARDWARE_RETRIEVAL_INDEX_MAPPING
    assert adapter.upsert_calls[0][1] == "KO-HW-W1C-001"
    assert adapter.upsert_calls[0][2]["contract_version"] == (
        "hardware-retrieval-index-document/v1"
    )
