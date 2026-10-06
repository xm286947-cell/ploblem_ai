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
            {
                "observation_id": "OBS-LIFE-A",
                "device_id": "emmc-golden",
                "device_type": "eMMC",
                "metric_name": "DEVICE_LIFE_TIME_EST_TYP_A",
                "raw_value": "0x03",
                "normalized_value": "0x03",
                "capture_time": "2026-10-06T08:00:00Z",
                "source_command_or_interface": "mmc extcsd read /dev/mmcblk0",
                "evidence_ref": "EVD-LIFE-A",
                "quality_status": "VALID",
                "availability_status": "AVAILABLE",
            },
            {
                "observation_id": "OBS-PRE-EOL",
                "device_id": "emmc-golden",
                "device_type": "eMMC",
                "metric_name": "PRE_EOL_INFO",
                "raw_value": "0x02",
                "normalized_value": "0x02",
                "capture_time": "2026-10-06T07:00:00Z",
                "source_command_or_interface": "mmc extcsd read /dev/mmcblk0",
                "evidence_ref": "EVD-PRE-EOL",
                "quality_status": "UNKNOWN",
                "availability_status": "AVAILABLE",
            },
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



class ReviewedSemanticKnowledgeConsumer:
    def __init__(self, *, include_s4=True):
        self.include_s4 = include_s4
        self.calls = []

    def status(self):
        return {
            "available": True,
            "status": "READY",
            "knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001",
        }

    @staticmethod
    def _item(
        object_id,
        *,
        semantic_class,
        parameter,
        consumers,
        object_type="DIAGNOSTIC",
    ):
        return {
            "object_id": object_id,
            "object_type": object_type,
            "status": "ACTIVE",
            "device_type": "NAND Flash",
            "title": object_id,
            "content": f"{semantic_class} for {parameter}",
            "tags": [
                "storage-lifetime",
                f"storage-parameter:{parameter}",
                f"storage-semantic:{semantic_class}",
            ],
            "evidence_refs": [f"EVD-{object_id}"],
            "source_refs": [f"SRC-{object_id}"],
            "metadata": {
                "storage_lifetime": {
                    "schema_version": "storage-lifetime-knowledge/v1",
                    "semantic_class": semantic_class,
                    "semantic_class_status": "REVIEWED",
                    "formal_consumable": True,
                    "canonical_parameters": [parameter],
                    "scenario_consumers": list(consumers),
                }
            },
        }

    def query(
        self,
        text,
        *,
        device_type="",
        top_k=8,
        knowledge_release_version=None,
        semantic_class="",
        canonical_parameter="",
        scenario_consumer="",
    ):
        self.calls.append({
            "text": text,
            "device_type": device_type,
            "scenario_consumer": scenario_consumer,
            "semantic_class": semantic_class,
            "canonical_parameter": canonical_parameter,
        })
        reviewed = [
            self._item(
                "KO-S3-PE",
                semantic_class="CALCULATION_RULE",
                parameter="pe_cycles",
                consumers=["S3"],
                object_type="FACT",
            ),
        ]
        if self.include_s4:
            reviewed.extend([
                self._item(
                    "KO-S4-ECC",
                    semantic_class="DIAGNOSTIC_RULE",
                    parameter="ecc_status",
                    consumers=["S4"],
                ),
                self._item(
                    "KO-S4-PERCENTAGE",
                    semantic_class="DIAGNOSTIC_RULE",
                    parameter="percentage_used",
                    consumers=["S4"],
                ),
            ])

        if scenario_consumer:
            reviewed = [
                item for item in reviewed
                if scenario_consumer
                in item["metadata"]["storage_lifetime"]["scenario_consumers"]
            ]
        if semantic_class:
            reviewed = [
                item for item in reviewed
                if item["metadata"]["storage_lifetime"]["semantic_class"]
                == semantic_class
            ]
        if canonical_parameter:
            reviewed = [
                item for item in reviewed
                if canonical_parameter
                in item["metadata"]["storage_lifetime"][
                    "canonical_parameters"
                ]
            ]

        if text:
            # A legacy prose hit that must never be used once this release
            # already contains reviewed Storage-lifetime metadata.
            legacy = {
                "object_id": "KO-LEGACY-PROSE",
                "object_type": "DIAGNOSTIC",
                "status": "ACTIVE",
                "device_type": device_type or "NAND Flash",
                "title": "Legacy diagnostic prose",
                "content": text,
                "evidence_refs": ["EVD-LEGACY"],
                "source_refs": ["SRC-LEGACY"],
                "metadata": {},
            }
            return {
                "knowledge_release_version": (
                    "KP-STORAGE-RC1-VALIDATION-001"
                ),
                "results": [legacy],
            }

        return {
            "knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001",
            "results": reviewed[:top_k],
        }


def test_reviewed_semantic_pack_query_selects_parameter_without_prose_guess():
    consumer = ReviewedSemanticKnowledgeConsumer()
    adapter = StorageDomainSkillAdapter(knowledge_consumer=consumer)

    result = adapter.query_pack(
        "PACK_DIAGNOSTIC_VALIDATION",
        "this prose should not drive selection",
        device_type="NAND Flash",
        semantic_classes=["DIAGNOSTIC_RULE"],
        canonical_parameters=["ecc_status"],
        scenario_consumer="S4",
    )

    assert result["status"] == "READY"
    assert result["selection_mode"] == "REVIEWED_STORAGE_SEMANTIC"
    assert [x["object_id"] for x in result["items"]] == ["KO-S4-ECC"]
    assert consumer.calls[0]["text"] == ""
    assert consumer.calls[0]["scenario_consumer"] == "S4"
    assert not any(call["text"] for call in consumer.calls)


def test_reviewed_model_release_missing_scenario_does_not_fall_back_to_legacy_prose():
    consumer = ReviewedSemanticKnowledgeConsumer(include_s4=False)
    adapter = StorageDomainSkillAdapter(knowledge_consumer=consumer)

    result = adapter.query_pack(
        "PACK_DIAGNOSTIC_VALIDATION",
        "legacy prose would have matched",
        device_type="NAND Flash",
        semantic_classes=["DIAGNOSTIC_RULE"],
        canonical_parameters=["ecc_status"],
        scenario_consumer="S4",
    )

    assert result["status"] == "INSUFFICIENT_KNOWLEDGE"
    assert result["selection_mode"] == "REVIEWED_STORAGE_SEMANTIC"
    assert result["items"] == []
    assert (
        "NO_MATCHING_REVIEWED_STORAGE_KNOWLEDGE_WITH_EVIDENCE"
        in result["missing_information"]
    )
    assert not any(call["text"] for call in consumer.calls)


def test_legacy_release_without_structured_contract_remains_compatible():
    adapter = StorageDomainSkillAdapter(
        knowledge_consumer=FakeKnowledgeConsumer()
    )

    result = adapter.query_pack(
        "PACK_CHANGE_IMPACT",
        "change impact",
        device_type="SSD",
        semantic_classes=["CHANGE_IMPACT_RULE"],
        canonical_parameters=["tbw"],
        scenario_consumer="S2",
    )

    assert result["status"] == "READY"
    assert result["selection_mode"] == "LEGACY_FORMAL_COMPATIBILITY"
    assert result["items"]



class UpgradedBindingKnowledgeConsumer(
    ReviewedSemanticKnowledgeConsumer
):
    def status(self):
        return {
            "available": True,
            "status": "READY",
            "knowledge_release_version": "KP-STORAGE-LIFETIME-20261006-R2",
        }

    def validate_storage_binding(self):
        return {
            "knowledge_release_version": (
                "KP-STORAGE-LIFETIME-20261006-R2"
            )
        }

    def query(
        self,
        text,
        *,
        device_type="",
        top_k=8,
        knowledge_release_version=None,
        semantic_class="",
        canonical_parameter="",
        scenario_consumer="",
    ):
        assert (
            knowledge_release_version
            == "KP-STORAGE-LIFETIME-20261006-R2"
        )
        result = super().query(
            text,
            device_type=device_type,
            top_k=top_k,
            knowledge_release_version=knowledge_release_version,
            semantic_class=semantic_class,
            canonical_parameter=canonical_parameter,
            scenario_consumer=scenario_consumer,
        )
        result["knowledge_release_version"] = (
            "KP-STORAGE-LIFETIME-20261006-R2"
        )
        return result


def test_controlled_binding_supersedes_legacy_pack_release_pin():
    adapter = StorageDomainSkillAdapter(
        knowledge_consumer=UpgradedBindingKnowledgeConsumer()
    )

    result = adapter.query_pack(
        "PACK_DIAGNOSTIC_VALIDATION",
        "ECC status",
        device_type="NAND Flash",
        semantic_classes=["DIAGNOSTIC_RULE"],
        canonical_parameters=["ecc_status"],
        scenario_consumer="S4",
    )

    assert result["status"] == "READY"
    assert (
        result["knowledge_release_version"]
        == "KP-STORAGE-LIFETIME-20261006-R2"
    )
    assert result["selection_mode"] == "REVIEWED_STORAGE_SEMANTIC"
