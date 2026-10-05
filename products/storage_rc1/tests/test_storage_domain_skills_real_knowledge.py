from __future__ import annotations

from fastapi.testclient import TestClient

from skills.real_knowledge import RealKnowledgeAssessmentService
from storage_life.app import app


def test_real_readiness_uses_current_formal_release_only():
    result = RealKnowledgeAssessmentService.current().readiness()

    assert result["formal_knowledge_object_count"] == 1
    assert result["knowledge_release"]["knowledge_release_version"] == "KP-STORAGE-RC1-VALIDATION-001"
    assert result["evidence_drilldown"]["status"] == "PASS"

    packs = result["packs"]
    assert packs["PACK_WRITE_GOVERNANCE"]["status"] == "BLOCKED"
    assert packs["PACK_WRITE_GOVERNANCE"]["formal_knowledge_object_count"] == 0

    for pack_id in [
        "PACK_LIFETIME_ENGINEERING",
        "PACK_DIAGNOSTIC_VALIDATION",
        "PACK_CHANGE_IMPACT",
    ]:
        assert packs[pack_id]["status"] == "PARTIAL"
        assert packs[pack_id]["formal_knowledge_object_count"] == 1
        assert packs[pack_id]["evidence_coverage"]["ratio"] == 1.0
        assert packs[pack_id]["release_trace_verified_sources"]["material_refs"] == ["EK-018"]

    assert packs["PACK_LIFETIME_ENGINEERING"]["catalog_verified_sources"]["count"] == 0


def test_evidence_drilldown_reaches_source_version_and_location():
    result = RealKnowledgeAssessmentService.current().readiness()
    traces = result["evidence_drilldown"]["object_traces"]

    assert len(traces) == 1
    trace = traces[0]
    assert trace["object_id"] == "KO-4ad47002b1ba4070fa499559"
    assert trace["trace_complete"] is True

    step = trace["steps"][0]
    assert step["evidence_id"] == "EVD-98019c742313d72837696c0b"
    assert step["source_ref"] == "NVME@2.0d"
    assert step["source"]["publisher"] == "NVM Express"
    assert step["source"]["title"] == "NVM Express Base Specification"
    assert step["source"]["version"] == "2.0d"
    assert step["source"]["archive_ref"] == "seed/03_SSD_NVME/NVM-Express-Base-2.0d.pdf"
    assert step["locator"]["value"]["page"] == 200


def test_real_golden_cases_are_partial_with_explicit_gaps():
    result = RealKnowledgeAssessmentService.current().real_golden()

    assert result["overall_status"] == "PARTIAL"
    assert result["formula_gap"] is False
    assert result["formula_task_required"] is False
    assert result["unknown_not_safe"] is True
    assert result["decision_boundary"] == "NO_AUTO_REPLACEMENT_DECISION"

    cases = result["cases"]
    assert set(cases) == {"RG01", "RG02", "RG03", "RG04", "RG05"}
    assert all(case["status"] == "PARTIAL" for case in cases.values())

    assert cases["RG01"]["knowledge_refs"] == []
    assert "FORMAL_WRITE_GOVERNANCE_KNOWLEDGE_NOT_RELEASED" in cases["RG01"]["unsupported_or_missing"]

    assert cases["RG02"]["knowledge_refs"] == ["KO-4ad47002b1ba4070fa499559"]
    assert cases["RG02"]["evidence_refs"] == ["EVD-98019c742313d72837696c0b"]
    assert cases["RG02"]["formula_gap"] is False
    assert "NVMe Percentage Used released semantics" in cases["RG02"]["supported_by_current_release"]
    assert "TARGET_SERVICE_LIFE_BUDGET_FORMULA_NOT_REGISTERED" not in cases["RG02"]["unsupported_or_missing"]

    assert "FORMAL_EMMC_LIFE_TIME_A_B_PRE_EOL_KNOWLEDGE_NOT_RELEASED" in cases["RG03"]["unsupported_or_missing"]
    assert "FORMAL_RAW_NAND_DIAGNOSTIC_KNOWLEDGE_NOT_RELEASED" in cases["RG04"]["unsupported_or_missing"]
    assert "TWO_PRODUCT_APPROVED_DEVICE_PROFILES_REQUIRED" in cases["RG05"]["unsupported_or_missing"]


def test_product_api_exposes_readiness_golden_and_fail_closed_skill_execution():
    client = TestClient(app)

    readiness = client.get("/api/product/skills/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["formal_knowledge_object_count"] == 1

    golden = client.get("/api/product/skills/real-golden")
    assert golden.status_code == 200
    assert golden.json()["cases"]["RG02"]["evidence_refs"] == ["EVD-98019c742313d72837696c0b"]

    write = client.post(
        "/api/product/skills/storage-write-governance/execute",
        json={
            "device_type": "SSD",
            "user_context": {"question": "high frequency small writes cache WAL fsync"},
            "workload_software_facts": [{"behavior": "small_frequent_write"}],
        },
    )
    assert write.status_code == 200
    payload = write.json()
    assert payload["status"] == "INSUFFICIENT_KNOWLEDGE"
    assert payload["evidence_refs"] == []
    assert payload["decision_boundary"] == "NO_AUTO_REPLACEMENT_DECISION"

    bad = client.post("/api/product/skills/not-a-skill/execute", json={})
    assert bad.status_code == 422


def test_source_priority_groups_expose_current_release_coverage():
    result = RealKnowledgeAssessmentService.current().readiness()
    groups = result["priority_source_groups"]

    assert groups["NVME_SMART_HEALTH"]["covered_by_current_release"] == ["EK-018"]
    assert groups["NAND_RAW_FLASH"]["covered_by_current_release"] == []
    assert groups["EMMC_STANDARD_HEALTH"]["covered_by_current_release"] == []
    assert groups["LINUX_WRITE_PATH"]["covered_by_current_release"] == []
    assert groups["SSD_ENDURANCE_WORKLOAD"]["covered_by_current_release"] == []
