from __future__ import annotations

import os
from pathlib import Path

import pytest

from services.hardware_retrieval_tagger import HardwareRetrievalTagger
from services.hardware_retrieval_tagger_runtime import (
    AGENT_ID,
    HardwareRetrievalTaggerRuntimeInvoker,
)


ROOT = Path(__file__).resolve().parents[1]


def projection() -> dict:
    return {
        "projection_schema_version": 1,
        "knowledge_id": "KO-HW-REAL-W1B-001",
        "public_ref": "HW-PUBLIC-REAL-W1B-R1",
        "business_case_id": "A9002",
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
                "evidence_refs": ["EV-REAL-1"],
                "status": "EXPLICIT",
            }
        ],
        "evidence_refs": ["EV-REAL-1"],
        "source_domain": "HARDWARE_CASE",
        "source_object_type": "HARDWARE_CASE",
        "formal_revision": 1,
        "formal_status": "ACTIVE",
        "formal_object_hash": "c" * 64,
        "projected_at": "2026-10-07T00:00:00+00:00",
    }


@pytest.mark.skipif(
    os.getenv("HARDWARE_RETRIEVAL_REAL_E2E") != "1",
    reason="real provider gate is opt-in",
)
def test_w1b_real_provider_tagging(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.update(
        {
            "HARDWARE_RETRIEVAL_MODEL_CONFIG": str(
                ROOT / "config/runtime/model.yaml"
            ),
            "HARDWARE_RETRIEVAL_RUNTIME_DB": str(tmp_path / "runtime.db"),
        }
    )
    invoker = HardwareRetrievalTaggerRuntimeInvoker(root=ROOT, environ=env)
    assert invoker.resolved.definition.agent_id == AGENT_ID
    assert invoker.resolved.provider.type == "openai_compatible"

    result = HardwareRetrievalTagger(invoker).tag(projection())

    assert result["contract_version"] == "hardware-retrieval-metadata/v1"
    assert result["knowledge_id"] == "KO-HW-REAL-W1B-001"
    assert result["tags"]
    assert result["tag_counts"]["FACT"] >= 1
    for tag in result["tags"]:
        if tag["kind"] == "EXPANSION":
            assert tag["usage"] == "RECALL_ONLY"
            assert tag["derived"] is True
            assert tag["claim_safe"] is False
        else:
            assert tag["usage"] == "RANK_AND_EXPLAIN"
            assert tag["claim_safe"] is True
