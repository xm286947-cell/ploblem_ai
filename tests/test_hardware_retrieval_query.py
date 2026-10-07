from __future__ import annotations

from copy import deepcopy

import pytest

from services.hardware_retrieval_index import build_index_document
from services.hardware_retrieval_metadata import build_retrieval_metadata
from services.hardware_retrieval_query import (
    SEARCH_FIELDS,
    HardwareRetrievalQueryError,
    HardwareRetrievalQueryService,
    build_why_hit,
)


def projection():
    return {
        "projection_schema_version": 1,
        "knowledge_id": "KO-W1D-001",
        "public_ref": "HW-PUBLIC-W1D-R1",
        "business_case_id": "A9201",
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
        "formal_object_hash": "f" * 64,
        "projected_at": "2026-10-07T00:00:00+00:00",
    }


def index_document():
    source = projection()
    metadata = build_retrieval_metadata(
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
    return build_index_document(source, metadata)


def test_why_hit_prefers_formal_claim_safe_reason():
    result = build_why_hit("偶发复位", index_document())
    assert result["status"] == "CLAIM_SAFE_MATCH"
    assert result["claim_safe"] is True
    assert result["reasons"][0]["reason_type"] == "FORMAL_FIELD"
    assert result["reasons"][0]["field"] == "symptom"


def test_expansion_hit_is_explicitly_not_claim_safe():
    result = build_why_hit("reboot", index_document())
    assert result["status"] == "EXPANSION_ASSISTED"
    assert result["claim_safe"] is False
    reason = result["reasons"][0]
    assert reason["reason_type"] == "EXPANSION_RECALL"
    assert reason["claim_safe"] is False
    assert reason["source_term"] == "偶发复位"
    assert reason["source_fields"] == ["symptom"]


def test_filter_only_has_no_fake_reason():
    result = build_why_hit("", index_document())
    assert result == {
        "status": "FILTER_ONLY",
        "claim_safe": True,
        "reasons": [],
    }


class StubAdapter:
    def __init__(self, source):
        self.source = source
        self.calls = []

    def search(self, index_or_alias, text="", *, filters=None, limit=20, fields=()):
        self.calls.append(
            {
                "index_or_alias": index_or_alias,
                "text": text,
                "filters": dict(filters or {}),
                "limit": limit,
                "fields": tuple(fields),
            }
        )
        return {
            "hits": [
                {
                    "_id": self.source["knowledge_id"],
                    "_score": 7.25,
                    "_source": self.source,
                }
            ]
        }


def test_query_service_reuses_w0_bm25_adapter_and_maps_filters():
    adapter = StubAdapter(index_document())
    service = HardwareRetrievalQueryService(
        adapter,
        index_alias="hardware-knowledge-search-active",
    )

    result = service.search(
        "偶发复位",
        filters={
            "business_case_id": "A9201",
            "interface": "CAN",
            "signal": "RESET_N",
            "device": "MCU",
        },
        limit=10,
    )

    call = adapter.calls[0]
    assert call["fields"] == SEARCH_FIELDS
    assert call["filters"] == {
        "formal_status": "ACTIVE",
        "source_domain": "HARDWARE_CASE",
        "source_object_type": "HARDWARE_CASE",
        "business_case_id": "A9201",
        "scope.interfaces": "CAN",
        "scope.signals": "RESET_N",
        "scope.devices": "MCU",
    }
    assert result["results"][0]["score"] == 7.25
    assert result["results"][0]["why_hit"]["claim_safe"] is True


def test_unknown_filter_fails_before_engine_call():
    adapter = StubAdapter(index_document())
    service = HardwareRetrievalQueryService(adapter, index_alias="active")
    with pytest.raises(HardwareRetrievalQueryError) as error:
        service.search("MCU", filters={"expansion_tag": "reboot"})
    assert error.value.code == "RETRIEVAL_FILTER_INVALID"
    assert adapter.calls == []


def test_mutated_expansion_claim_boundary_fails_closed():
    source = index_document()
    broken = deepcopy(source)
    expansion = [
        item for item in broken["tag_provenance"]
        if item["kind"] == "EXPANSION"
    ][0]
    expansion["claim_safe"] = True
    with pytest.raises(HardwareRetrievalQueryError) as error:
        build_why_hit("reboot", broken)
    assert error.value.code == "RETRIEVAL_HIT_CLAIM_BOUNDARY_INVALID"


def test_malformed_engine_hit_fails_closed():
    source = index_document()
    source["contract_version"] = "bad/v0"
    adapter = StubAdapter(source)
    service = HardwareRetrievalQueryService(adapter, index_alias="active")
    with pytest.raises(HardwareRetrievalQueryError) as error:
        service.search("MCU")
    assert error.value.code == "RETRIEVAL_HIT_CONTRACT_INVALID"
