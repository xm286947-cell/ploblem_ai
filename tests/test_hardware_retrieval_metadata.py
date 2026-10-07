from __future__ import annotations

from copy import deepcopy

import pytest

from services.hardware_retrieval_metadata import (
    EXPANSION,
    FACT,
    NORMALIZED,
    RECALL_ONLY,
    RANK_AND_EXPLAIN,
    HardwareRetrievalMetadataError,
    build_retrieval_metadata,
)
from services.hardware_retrieval_tagger import (
    HardwareRetrievalTagger,
    HardwareRetrievalTaggerError,
    TAGGER_INPUT_CONTRACT_VERSION,
)


def projection():
    return {
        "projection_schema_version": 1,
        "knowledge_id": "KO-HW-001",
        "public_ref": "HW-PUBLIC-A0152-R1",
        "business_case_id": "A0152",
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
        "key_parameters": [
            {"name": "VDD", "value": "3.3 V", "unit": "V"},
        ],
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
        "formal_object_hash": "a" * 64,
        "projected_at": "2026-10-07T00:00:00+00:00",
    }


def valid_tags():
    return [
        {
            "term": "偶发复位",
            "kind": FACT,
            "source_term": "偶发复位",
            "source_fields": ["symptom"],
        },
        {
            "term": "reset-n",
            "kind": NORMALIZED,
            "source_term": "RESET_N",
            "source_fields": ["signal"],
        },
        {
            "term": "reboot",
            "kind": EXPANSION,
            "source_term": "偶发复位",
            "source_fields": ["symptom"],
        },
    ]


def test_metadata_contract_separates_fact_normalized_and_expansion():
    result = build_retrieval_metadata(projection(), valid_tags())

    assert result["contract_version"] == "hardware-retrieval-metadata/v1"
    assert result["knowledge_id"] == "KO-HW-001"
    assert result["source"]["contract_version"] == "hardware-knowledge-consumption/v1"
    assert result["tag_counts"] == {
        FACT: 1,
        NORMALIZED: 1,
        EXPANSION: 1,
    }

    fact, normalized, expansion = result["tags"]
    assert fact["kind"] == FACT
    assert fact["usage"] == RANK_AND_EXPLAIN
    assert fact["derived"] is False
    assert fact["claim_safe"] is True

    assert normalized["kind"] == NORMALIZED
    assert normalized["usage"] == RANK_AND_EXPLAIN
    assert normalized["claim_safe"] is True

    assert expansion["kind"] == EXPANSION
    assert expansion["usage"] == RECALL_ONLY
    assert expansion["derived"] is True
    assert expansion["claim_safe"] is False


def test_fact_must_be_literal_source_supported():
    with pytest.raises(HardwareRetrievalMetadataError) as error:
        build_retrieval_metadata(
            projection(),
            [{
                "term": "过温",
                "kind": FACT,
                "source_term": "过温",
                "source_fields": ["symptom"],
            }],
        )
    assert error.value.code == "TAG_SOURCE_TERM_UNSUPPORTED"


def test_fact_cannot_change_the_literal_phrase():
    with pytest.raises(HardwareRetrievalMetadataError) as error:
        build_retrieval_metadata(
            projection(),
            [{
                "term": "reboot",
                "kind": FACT,
                "source_term": "偶发复位",
                "source_fields": ["symptom"],
            }],
        )
    assert error.value.code == "TAG_FACT_NOT_LITERAL"


def test_normalized_is_mechanical_only_not_semantic_aliasing():
    with pytest.raises(HardwareRetrievalMetadataError) as error:
        build_retrieval_metadata(
            projection(),
            [{
                "term": "reboot",
                "kind": NORMALIZED,
                "source_term": "偶发复位",
                "source_fields": ["symptom"],
            }],
        )
    assert error.value.code == "TAG_NORMALIZED_NOT_EQUIVALENT"


def test_normalized_accepts_case_and_punctuation_only():
    result = build_retrieval_metadata(
        projection(),
        [{
            "term": "reset-n",
            "kind": NORMALIZED,
            "source_term": "RESET_N",
            "source_fields": ["signal"],
        }],
    )
    assert result["tags"][0]["kind"] == NORMALIZED


def test_expansion_requires_real_source_anchor_and_stays_recall_only():
    with pytest.raises(HardwareRetrievalMetadataError) as error:
        build_retrieval_metadata(
            projection(),
            [{
                "term": "reboot",
                "kind": EXPANSION,
                "source_term": "thermal shutdown",
                "source_fields": ["symptom"],
            }],
        )
    assert error.value.code == "TAG_SOURCE_TERM_UNSUPPORTED"

    result = build_retrieval_metadata(
        projection(),
        [{
            "term": "reboot",
            "kind": EXPANSION,
            "source_term": "偶发复位",
            "source_fields": ["symptom"],
        }],
    )
    assert result["tags"][0]["usage"] == RECALL_ONLY
    assert result["tags"][0]["claim_safe"] is False


def test_scope_is_explicit_only():
    result = build_retrieval_metadata(projection(), [])
    assert result["scope"] == {
        "business_case_id": "A0152",
        "source_domain": "HARDWARE_CASE",
        "source_object_type": "HARDWARE_CASE",
        "interfaces": ["CAN"],
        "signals": ["RESET_N"],
        "devices": ["MCU", "STM32 MCU"],
    }
    assert "STMicroelectronics" not in str(result)


def test_output_is_deterministic_and_does_not_mutate_projection():
    source = projection()
    before = deepcopy(source)
    first = build_retrieval_metadata(source, valid_tags())
    second = build_retrieval_metadata(source, list(reversed(valid_tags())))
    assert first == second
    assert source == before


def test_invalid_or_inactive_formal_source_fails_closed():
    source = projection()
    source["formal_status"] = "STALE"
    with pytest.raises(HardwareRetrievalMetadataError) as error:
        build_retrieval_metadata(source, [])
    assert error.value.code == "RETRIEVAL_SOURCE_INVALID"


def test_unknown_source_field_fails_closed():
    with pytest.raises(HardwareRetrievalMetadataError) as error:
        build_retrieval_metadata(
            projection(),
            [{
                "term": "MCU",
                "kind": FACT,
                "source_term": "MCU",
                "source_fields": ["invented_field"],
            }],
        )
    assert error.value.code == "TAG_SOURCE_FIELD_INVALID"


def test_tagger_exposes_only_allowlisted_formal_projection_values():
    seen = {}

    def invoke(request):
        seen.update(request)
        return {"tags": valid_tags()}

    result = HardwareRetrievalTagger(invoke).tag(projection())

    assert seen["contract_version"] == TAGGER_INPUT_CONTRACT_VERSION
    assert seen["knowledge_id"] == "KO-HW-001"
    assert "evidence_refs" not in seen["fields"]
    assert "projected_at" not in seen["fields"]
    assert seen["fields"]["signal"] == ["RESET_N"]
    assert result["contract_version"] == "hardware-retrieval-metadata/v1"


def test_tagger_invalid_model_output_fails_closed():
    with pytest.raises(HardwareRetrievalTaggerError) as error:
        HardwareRetrievalTagger(lambda request: {"unexpected": []}).tag(projection())
    assert error.value.code == "TAGGER_RESPONSE_INVALID"


def test_tagger_provider_exception_is_stable_error():
    def invoke(_request):
        raise RuntimeError("provider unavailable")

    with pytest.raises(HardwareRetrievalTaggerError) as error:
        HardwareRetrievalTagger(invoke).tag(projection())
    assert error.value.code == "TAGGER_INVOCATION_FAILED"
