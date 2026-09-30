from __future__ import annotations

from pathlib import Path

import yaml

from skills.runtime_adapter import StorageDomainSkillAdapter
from storage_life.lifetime_engine import ConfirmedFact, LifetimeAssessmentRequest


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


class FakeKnowledgeConsumer:
    def status(self):
        return {
            "available": True,
            "status": "READY",
            "knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001",
        }

    def query(self, text, *, device_type="", top_k=8, knowledge_release_version=None):
        lower = text.lower()
        if "health" in lower or "life_time" in lower or "pre_eol" in lower or "nand" in lower:
            object_type = "DIAGNOSTIC"
            title = "Diagnostic method"
            content = "Collect the device metric from the released protocol/tool path and interpret only within released semantics."
        elif "change" in lower or "impact" in lower:
            object_type = "CONCEPT"
            title = "Change impact concept"
            content = "Parameter deltas require software, monitoring, and validation review when the released evidence supports the mechanism."
        else:
            object_type = "SOLUTION"
            title = "Write governance"
            content = "High-frequency small writes may increase write amplification; batching/coalescing controls require workload validation."
        return {
            "knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001",
            "results": [{
                "object_id": "KO-GOLDEN-001",
                "object_type": object_type,
                "status": "ACTIVE",
                "device_type": device_type or "Generic",
                "title": title,
                "content": content,
                "evidence_refs": ["EVD-GOLDEN-001"],
                "source_refs": ["SRC-GOLDEN-001"],
            }],
        }


def test_material_inventory_ek001_028_and_two_axis_classification():
    payload = yaml.safe_load((SKILLS / "material_inventory.yaml").read_text(encoding="utf-8"))
    materials = payload["materials"]
    assert len(materials) == 28
    assert [x["material_id"] for x in materials] == [f"EK-{i:03d}" for i in range(1, 29)]
    allowed_source = {
        "A1_DEVICE_SPEC_OR_STANDARD",
        "A2_VENDOR_ENGINEERING_GUIDE",
        "A3_OS_AND_OPEN_SOURCE_ENGINEERING",
        "A4_INDUSTRY_METHOD",
    }
    allowed_groups = {
        "G1_DEVICE_MECHANISM_AND_SPEC",
        "G2_SOFTWARE_WRITE_GOVERNANCE",
        "G3_LIFETIME_BUDGET_AND_MODEL",
        "G4_DIAGNOSTIC_AND_VALIDATION",
        "G5_CHANGE_IMPACT_SUPPORT",
    }
    for item in materials:
        assert item["source_class"] in allowed_source
        assert item["consumption_domains"]
        assert set(item["consumption_domains"]) <= allowed_groups
        assert item["target_knowledge_object_types"]
        assert "priority" in item and "current_gap" in item


def test_four_pack_manifests_are_formal_release_views():
    for name in ["write_governance", "lifetime_engineering", "diagnostic_validation", "change_impact"]:
        payload = yaml.safe_load((SKILLS / "packs" / f"{name}.yaml").read_text(encoding="utf-8"))
        assert payload["view_type"] == "FORMAL_KNOWLEDGE_RELEASE_FILTER"
        assert payload["required_evidence"]["formal_release_only"] is True
        assert payload["required_evidence"]["evidence_refs_required"] is True
        assert payload["knowledge_release_dependency"]["contract_version"] == "knowledge-query/v1"
        assert payload["material_refs"]


def test_gs01_write_governance_evidence_backed():
    adapter = StorageDomainSkillAdapter(knowledge_consumer=FakeKnowledgeConsumer())
    result = adapter.execute_write_governance(
        device_type="SSD",
        user_context={"question": "high-frequency small writes write amplification controls"},
        workload_software_facts=[{"behavior": "small_frequent_write", "source": "declared"}],
    )
    assert result["status"] == "ANSWERED"
    assert result["evidence_refs"] == ["EVD-GOLDEN-001"]
    assert result["structured_result"]["engineering_control_options"]
    assert result["decision_boundary"] == "NO_AUTO_REPLACEMENT_DECISION"


def test_gs02_lifetime_budget_uses_existing_engine_and_fails_closed_for_unregistered_target_life_budget():
    adapter = StorageDomainSkillAdapter(knowledge_consumer=FakeKnowledgeConsumer())
    request = LifetimeAssessmentRequest(
        device_id="ssd-golden",
        confirmed_facts=[
            ConfirmedFact(
                fact_id="FACT-HOST",
                metric_name="host_written_bytes",
                value=100,
                unit="GB",
                evidence_refs=["EVD-HOST"],
            ),
            ConfirmedFact(
                fact_id="FACT-TBW",
                metric_name="rated_tbw_bytes",
                value=1,
                unit="TB",
                evidence_refs=["EVD-TBW"],
            ),
        ],
    )
    result = adapter.execute_lifetime_budget(
        request=request,
        requested_metric="ssd.tbw",
        target_service_life={"value": 10, "unit": "years"},
    )
    assert result["status"] == "PARTIAL"
    assert result["structured_result"]["applicable_formula"]["formula_id"] == "SSD_TBW_CONSUMPTION_V1"
    assert result["structured_result"]["write_budget"]["rated_tbw_bytes"] == 1_000_000_000_000
    assert "TARGET_SERVICE_LIFE_BUDGET_FORMULA_NOT_REGISTERED" in result["missing_information"]
    assert "remaining_years" not in str(result)


def test_gs03_emmc_diagnostic_separates_capability_from_current_observation():
    adapter = StorageDomainSkillAdapter(knowledge_consumer=FakeKnowledgeConsumer())
    result = adapter.execute_diagnostic_validation(
        device_type="eMMC",
        target_question="DEVICE_LIFE_TIME_EST_TYP_A PRE_EOL health interpretation",
        diagnostic_capabilities=[{"metric": "DEVICE_LIFE_TIME_EST_TYP_A", "supported": True}],
        runtime_observations=[
            {"metric": "DEVICE_LIFE_TIME_EST_TYP_A", "value": "0x03", "is_formally_consumable": True},
            {"metric": "PRE_EOL_INFO", "value": "0x02", "is_formally_consumable": False},
        ],
    )
    assert result["status"] == "ANSWERED"
    assert len(result["structured_result"]["current_observation"]) == 1
    assert "STALE_OR_NONCONSUMABLE_RUNTIME_OBSERVATION_EXCLUDED" in result["missing_information"]
    assert result["evidence_refs"] == ["EVD-GOLDEN-001"]


def test_gs04_raw_nand_diagnostic_is_evidence_backed():
    adapter = StorageDomainSkillAdapter(knowledge_consumer=FakeKnowledgeConsumer())
    result = adapter.execute_diagnostic_validation(
        device_type="NAND",
        target_question="NAND P/E erase block wear distribution diagnostic observations",
        diagnostic_capabilities=[{"metric": "erase_count", "supported": True}],
        runtime_observations=[{"metric": "erase_count", "value": 120, "is_formally_consumable": True}],
    )
    assert result["status"] == "ANSWERED"
    assert result["structured_result"]["validation_method"]
    assert result["evidence_refs"]


def test_gs05_change_impact_unknown_never_becomes_safe():
    adapter = StorageDomainSkillAdapter(knowledge_consumer=FakeKnowledgeConsumer())
    result = adapter.execute_change_impact(
        device_type="SSD",
        parameter_delta=[{"field": "tbw", "old": 1200, "new": 600}],
        question="change impact tbw health software monitoring validation",
    )
    assert result["status"] == "ANSWERED"
    assert set(result["structured_result"]["impact_classification"]) <= {
        "KNOWLEDGE_BACKED", "DERIVED", "AI_HYPOTHESIS", "UNKNOWN"
    }
    assert result["decision_boundary"] == "NO_AUTO_REPLACEMENT_DECISION"
    assert "approve" not in result["direct_answer"].lower()


class EmptyKnowledgeConsumer(FakeKnowledgeConsumer):
    def query(self, *args, **kwargs):
        return {"knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001", "results": []}


def test_fail_closed_without_matching_formal_knowledge():
    adapter = StorageDomainSkillAdapter(knowledge_consumer=EmptyKnowledgeConsumer())
    result = adapter.execute_write_governance(
        device_type="SSD",
        user_context={"question": "unknown write behavior"},
    )
    assert result["status"] == "INSUFFICIENT_KNOWLEDGE"
    assert result["evidence_refs"] == []
    assert "NO_MATCHING_RELEASED_KNOWLEDGE_WITH_EVIDENCE" in result["missing_information"]
