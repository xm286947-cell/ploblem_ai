"""Scenario-level golden tests for the four authorized synthetic workflows."""
from __future__ import annotations

import json
from pathlib import Path

from services.hardware_case_scenario_poc import ScenarioPoC


GOLDEN = Path(__file__).parent / "golden/hardware_case_scenarios"


def test_s1_s4_all_twelve_synthetic_cases(tmp_path):
    expected = json.loads((GOLDEN / "expected.json").read_text(encoding="utf-8"))
    assert len(expected) == 12
    poc = ScenarioPoC(tmp_path / "poc.sqlite3", GOLDEN / "circuit_feature.xlsx", GOLDEN / "material.xlsx")
    outcomes = {}
    for word in sorted(GOLDEN.glob("*.docx")):
        result = poc.ingest(word)
        case_id = result["case_id"]
        outcomes[case_id] = result["status"]
        detail = poc.query("case_id", case_id)[0]
        answer = expected[case_id]
        for field in ("title", "background", "symptom", "analysis_process", "root_cause", "actions", "conclusion"):
            assert detail[field] == answer[field], (case_id, field)
        for field in answer["expected_evidence_fields"]:
            refs = detail["evidence_refs"][field]
            assert refs, (case_id, field)
            for ref in refs:
                evidence = detail["evidence"][ref]
                assert detail[field] in evidence["excerpt_or_caption"]
                assert evidence["locator"]["block_id"]
        assert {link["node_id"] for link in detail["circuit_feature_links"]} == set(answer["circuit_feature_links"])
        assert {link["node_id"] for link in detail["material_links"]} == set(answer["material_links"])
        assert all(link["mapping_status"] == "SUGGESTED" for link in detail["circuit_feature_links"] + detail["material_links"])
    assert set(outcomes) == set(expected)
    assert outcomes["A9008"] == "NEEDS_REVIEW"
    assert outcomes["A9009"] == "NEEDS_REVIEW"
    # S1/S2: candidate node queries use their respective independent tree only.
    assert {item["case_id"] for item in poc.query("circuit", "CF-LDO")} == {"A9001", "A9008"}
    assert {item["case_id"] for item in poc.query("material", "MD-CAP")} == {"A9001", "A9006", "A9010"}
    assert poc.query("material", "CF-LDO") == []
    # S3: symptom-only keyword retrieval, including an unmapped case.
    assert {item["case_id"] for item in poc.query("symptom", "指示灯闪烁")} == {"A9012"}
    assert {item["case_id"] for item in poc.query("symptom", "输出振荡")} == {"A9001"}
    assert poc.query("circuit", "CF-NOT-FOUND") == []


def test_unsupported_fact_fails_closed(tmp_path):
    from repositories.hardware_case_repository import HardwareCaseRepository
    from services.hardware_case_ai_adapter import HardwareCaseAIAdapter
    from services.hardware_case_backend import HardwareCaseBackendService

    service = HardwareCaseBackendService(HardwareCaseRepository(tmp_path / "negative.sqlite3"))

    def bad(document):
        block = next(item for item in document["blocks"] if item["block_type"] == "PARAGRAPH")
        return {"facts": {"root_cause": {"value": "不存在的根因", "evidence_block_ids": [block["block_id"]]}}}

    result = HardwareCaseAIAdapter(service, bad).ingest_docx(next(GOLDEN.glob("A9001*.docx")))
    assert result["status"] == "NEEDS_REVIEW"
    assert "KEY_FACT_UNSUPPORTED:root_cause" in result["warnings"]
    case = service.get_case("A9001", role="MAINTAINER")
    assert case["facts"]["root_cause"]["evidence_refs"] == []
    assert case["facts"]["root_cause"]["confirmed_value"] is None
