from storage_life import ai, parameter_baseline, templates, product_api
from skills import real_knowledge


def test_359_ssd_direct_fact_contract_restores_frozen_fields():
    fields = {x["canonical_name"] for x in ai.expected_fields("SSD")}
    assert {
        "capacity",
        "nand_type",
        "interface",
        "protocol",
        "host_memory_buffer",
        "operating_temperature",
        "tbw",
        "plp",
    } <= fields


def test_359_timar_read_plan_targets_frozen_direct_fact_fields():
    pages = [
        (
            2,
            "KEY FEATURES\nCapacity 256GB/512GB/1TB/2TB\n"
            "PCIe Gen 4 interface with up to 4 lanes\n"
            "NVMe Revision 2.0\nSupporting host memory buffer\n"
            "TBW 768/1500/3000/6000 TB (WAF=1)\n"
            "Operating -25C to +85C\nPLP Optional",
            "text",
        ),
        (
            5,
            "PRODUCT LINE-UP\nTIMAR K97M8-Y 256GB SSD\n"
            "TIMAR K97M8-Y 1TB SSD",
            "text",
        ),
    ]
    plan = templates.build_read_plan(pages, "SSD", "TIMAR")
    targets = {field for row in plan for field in row.get("target_fields", [])}
    assert {
        "capacity",
        "nand_type",
        "interface",
        "protocol",
        "host_memory_buffer",
        "operating_temperature",
        "tbw",
        "plp",
    } <= targets


def test_359_ssd_product_baseline_keeps_hmb_and_interface_protocol_mapping():
    fields = parameter_baseline.product_fields("SSD", ai.expected_fields("SSD"))
    by_name = {x["canonical_name"]: x for x in fields}
    assert "host_memory_buffer" in by_name
    assert by_name["host_memory_buffer"]["requirement_level"] == "SHOULD"
    assert {"interface", "protocol"} <= set(by_name["interface_protocol"]["aliases"])


def test_359_nand_bad_block_observability_is_not_runtime_bad_block_alias():
    fields = parameter_baseline.product_fields("NAND Flash", ai.expected_fields("NAND Flash"))
    by_name = {x["canonical_name"]: x for x in fields}
    assert "bad_block_observability" in by_name
    assert "runtime_bad_block" not in by_name["bad_block_observability"]["aliases"]
    # The descriptive runtime-bad-block fact remains independently visible; it is not
    # promoted into an observability capability without an explicit acquisition mechanism.
    assert "runtime_bad_block" in by_name


def test_359_diagnostic_status_distinguishes_datasheet_knowledge_and_na():
    field = {
        "canonical_name": "percentage_used",
        "group": parameter_baseline.KEY_DIAGNOSTIC,
    }
    explicit = product_api._diagnostic_semantics(
        field=field,
        coverage_status="FOUND",
        primary={"ai_value": "supported"},
        evidence=[{"source_id": "s", "source_page": 1}],
        formal_knowledge={"status": "NO_MATCH"},
    )
    assert explicit["status"] == "DATASHEET_EXPLICIT"

    standard = product_api._diagnostic_semantics(
        field=field,
        coverage_status="NOT_FOUND",
        primary=None,
        evidence=[],
        formal_knowledge={"status": "MATCHED"},
    )
    assert standard["status"] == "STANDARD_APPLICABLE_REQUIRES_DEVICE_VALIDATION"

    gap = product_api._diagnostic_semantics(
        field=field,
        coverage_status="NOT_FOUND",
        primary=None,
        evidence=[],
        formal_knowledge={"status": "UNKNOWN", "code": "KNOWLEDGE_RELEASE_NOT_READY"},
    )
    assert gap["status"] == "KNOWLEDGE_GAP"

    not_applicable = product_api._diagnostic_semantics(
        field=field,
        coverage_status="NOT_APPLICABLE",
        primary=None,
        evidence=[],
        formal_knowledge={"status": "MATCHED"},
    )
    assert not_applicable["status"] == "NOT_APPLICABLE"


def test_359_explicit_unsupported_is_not_collapsed_into_not_found():
    field = {
        "canonical_name": "smart_health",
        "group": parameter_baseline.KEY_DIAGNOSTIC,
    }
    result = product_api._diagnostic_semantics(
        field=field,
        coverage_status="FOUND",
        primary={"ai_value": "not supported"},
        evidence=[{"source_id": "s", "source_page": 2}],
        formal_knowledge={"status": "MATCHED"},
    )
    assert result["status"] == "EXPLICITLY_NOT_SUPPORTED"


def test_359_search_coverage_is_distinct_from_fact_coverage():
    slots = [
        {"coverage_status": "FOUND", "review_status": "CONFIRMED", "value": "x"},
        {"coverage_status": "NOT_FOUND", "review_status": "NOT_REVIEWED", "value": None},
        {"coverage_status": "FOUND", "review_status": "UNREVIEWED", "value": None},
        {"coverage_status": "NOT_APPLICABLE", "review_status": "NOT_REVIEWED", "value": None},
    ]
    metrics = product_api._coverage_metrics(slots)
    assert metrics["search_coverage_ratio"] == 1.0
    assert metrics["fact_coverage_ratio"] == 0.3333


class _FakeSkillService:
    def __init__(self):
        self.calls = []

    def execute_skill(self, skill_id, payload):
        self.calls.append((skill_id, payload))
        return {
            "skill_id": skill_id,
            "status": "INSUFFICIENT_KNOWLEDGE",
            "missing_information": ["FORMAL_KNOWLEDGE_RELEASE_REQUIRED"],
        }


def test_359_product_diagnostics_reuses_existing_domain_skill(monkeypatch):
    service = _FakeSkillService()
    monkeypatch.setattr(
        real_knowledge.RealKnowledgeAssessmentService,
        "current",
        classmethod(lambda cls: service),
    )
    monkeypatch.setattr(product_api, "device_slots", lambda device_id: {
        "device": {"id": device_id, "device_type": "SSD"},
        "lifecycle": {"formal_ready": True, "status": "FORMAL_READY"},
        "slots": [
            {
                "canonical_name": "percentage_used",
                "parameter_name": "Percentage Used",
                "group": parameter_baseline.KEY_DIAGNOSTIC,
                "review_status": "NOT_REVIEWED",
                "status": "NOT_FOUND",
                "diagnostic_status": "KNOWLEDGE_GAP",
                "diagnostic_label": "知识缺口",
                "value": None,
                "evidence": [],
                "formal_knowledge": {
                    "status": "UNKNOWN",
                    "code": "KNOWLEDGE_RELEASE_NOT_READY",
                    "results": [],
                    "evidence_refs": [],
                },
            }
        ],
    })
    result = product_api.diagnostics(device_id="ssd-1")
    assert result["layers"] == ["DATASHEET_FACT", "RUNTIME_OBSERVATION", "KNOWLEDGE"]
    assert result["semantic_layers"] == ["DATASHEET_FACT", "DOMAIN_KNOWLEDGE", "RUNTIME_OBSERVATION"]
    assert result["skill_id"] == "storage-diagnostic-validation"
    assert result["skill_result"]["status"] == "INSUFFICIENT_KNOWLEDGE"
    assert service.calls
    skill_id, payload = service.calls[0]
    assert skill_id == "storage-diagnostic-validation"
    assert payload["device_type"] == "SSD"
    assert payload["diagnostic_capabilities"][0]["canonical_name"] == "percentage_used"


def test_359_change_impact_reuses_existing_domain_skill(monkeypatch):
    service = _FakeSkillService()
    monkeypatch.setattr(
        real_knowledge.RealKnowledgeAssessmentService,
        "current",
        classmethod(lambda cls: service),
    )
    monkeypatch.setattr(product_api, "compare_devices", lambda device_ids: {
        "devices": [
            {"id": device_ids[0], "device_type": "SSD"},
            {"id": device_ids[1], "device_type": "SSD"},
        ],
        "rows": [
            {
                "canonical_name": "tbw",
                "parameter_name": "TBW",
                "is_difference": True,
                "has_missing": False,
                "cells": {
                    device_ids[0]: {
                        "status": "CONFIRMED",
                        "review_status": "CONFIRMED",
                        "value": "768",
                        "unit": "TB",
                        "evidence": [{"source_id": "old"}],
                    },
                    device_ids[1]: {
                        "status": "CONFIRMED",
                        "review_status": "CONFIRMED",
                        "value": "3000",
                        "unit": "TB",
                        "evidence": [{"source_id": "new"}],
                    },
                },
            }
        ],
    })
    monkeypatch.setattr(product_api, "_formal_knowledge", lambda *args, **kwargs: {
        "status": "UNKNOWN",
        "code": "KNOWLEDGE_RELEASE_NOT_READY",
        "results": [],
        "evidence_refs": [],
    })
    result = product_api.change_impact("old", "new")
    assert result["skill_id"] == "storage-change-impact"
    assert result["skill_result"]["status"] == "INSUFFICIENT_KNOWLEDGE"
    assert service.calls[0][0] == "storage-change-impact"
    assert service.calls[0][1]["parameter_delta"][0]["canonical_name"] == "tbw"


def test_359_ssd_semantic_rule_forbids_cell_type_inference_from_generic_nand():
    rules = ai._semantic_rules("SSD")
    assert "cell_type requires explicit SLC/MLC/TLC/QLC" in rules
    assert "never infer TLC/QLC" in rules


def test_359_product_knowledge_gap_is_explicit_partial(monkeypatch):
    service = _FakeSkillService()
    monkeypatch.setattr(
        real_knowledge.RealKnowledgeAssessmentService,
        "current",
        classmethod(lambda cls: service),
    )
    monkeypatch.setattr(product_api, "device_slots", lambda device_id: {
        "device": {"id": device_id, "device_type": "SSD"},
        "lifecycle": {"formal_ready": True, "status": "FORMAL_READY"},
        "slots": [{
            "canonical_name": "percentage_used",
            "parameter_name": "Percentage Used",
            "group": parameter_baseline.KEY_DIAGNOSTIC,
            "review_status": "NOT_REVIEWED",
            "status": "NOT_FOUND",
            "diagnostic_status": "KNOWLEDGE_GAP",
            "diagnostic_label": "知识缺口",
            "value": None,
            "evidence": [],
            "formal_knowledge": {
                "status": "UNKNOWN",
                "code": "KNOWLEDGE_RELEASE_NOT_READY",
                "results": [],
                "evidence_refs": [],
            },
        }],
    })
    result = product_api.diagnostics(device_id="ssd-gap")
    assert result["result_status"] == "PARTIAL"
    assert result["knowledge_gap"] is True
    assert result["skill_result"]["status"] == "INSUFFICIENT_KNOWLEDGE"


class _FakeKnowledgeStatus:
    def status(self):
        return {
            "available": False,
            "status": "NOT_READY",
            "code": "FORMAL_KNOWLEDGE_RELEASE_REQUIRED",
        }


def _selected_device_detail():
    return {
        "device": {"id": "ssd-ctx", "device_type": "SSD", "vendor": "TIMAR", "model": "K97M8-Y"},
        "device_facts": [
            {
                "canonical_name": "tbw",
                "parameter_name": "TBW",
                "value": "768",
                "unit": "TB",
                "condition": "WAF=1",
                "scope": "256GB",
                "evidence": [{"source_id": "TIMAR-97", "evidence_id": "EVD-TBW"}],
            }
        ],
        "slots": [
            {
                "canonical_name": "percentage_used",
                "group": parameter_baseline.KEY_DIAGNOSTIC,
                "diagnostic_status": "KNOWLEDGE_GAP",
                "value": None,
                "review_status": "NOT_REVIEWED",
                "evidence": [],
            }
        ],
    }


def test_359_selected_device_context_routes_all_four_existing_skills(monkeypatch):
    service = _FakeSkillService()
    monkeypatch.setattr(
        real_knowledge.RealKnowledgeAssessmentService,
        "current",
        classmethod(lambda cls: service),
    )
    monkeypatch.setattr(product_api, "device_slots", lambda device_id: _selected_device_detail())
    monkeypatch.setattr(
        product_api.KnowledgeReleaseConsumer,
        "current",
        classmethod(lambda cls: _FakeKnowledgeStatus()),
    )

    write = product_api.execute_device_skill(
        "ssd-ctx",
        "storage-write-governance",
        {"user_context": {"question": "small writes"}},
    )
    diagnostic = product_api.execute_device_skill(
        "ssd-ctx",
        "storage-diagnostic-validation",
        {"runtime_observations": []},
    )
    change = product_api.execute_device_skill(
        "ssd-ctx",
        "storage-change-impact",
        {"parameter_delta": [{"canonical_name": "tbw", "old": "768", "new": "3000"}]},
    )
    lifetime = product_api.execute_device_skill(
        "ssd-ctx",
        "storage-lifetime-budget",
        {"requested_metric": "ssd.tbw", "assessment_request": {}},
    )

    assert [call[0] for call in service.calls] == [
        "storage-write-governance",
        "storage-diagnostic-validation",
        "storage-change-impact",
        "storage-lifetime-budget",
    ]
    assert write["adapter"] == "EXISTING_STORAGE_DOMAIN_SKILL_ADAPTER"
    assert write["second_skill_stack"] is False
    assert write["second_knowledge_stack"] is False
    assert write["context"]["confirmed_device_facts"][0]["canonical_name"] == "tbw"
    assert diagnostic["skill_payload"]["diagnostic_capabilities"][0]["canonical_name"] == "percentage_used"
    assert change["skill_payload"]["parameter_delta"][0]["canonical_name"] == "tbw"
    life_facts = lifetime["skill_payload"]["assessment_request"]["confirmed_facts"]
    assert life_facts[0]["metric_name"] == "rated_tbw_bytes"
    assert life_facts[0]["evidence_refs"] == ["EVD-TBW"]


def test_359_engineering_result_has_required_product_shape():
    view = product_api._engineering_result_view({
        "skill_id": "storage-diagnostic-validation",
        "status": "INSUFFICIENT_KNOWLEDGE",
        "direct_answer": "knowledge missing",
        "structured_result": {"validation_method": ["read health log"]},
        "fact_derived_hypothesis_separation": {
            "facts": [{"metric": "protocol", "value": "NVMe 2.0"}],
            "derived": [],
            "hypotheses": [],
            "unknowns": ["runtime"],
        },
        "knowledge_refs": [],
        "evidence_refs": ["EVD-PROTOCOL"],
        "missing_information": ["FORMAL_KNOWLEDGE_RELEASE_REQUIRED"],
        "decision_boundary": "NO_AUTO_REPLACEMENT_DECISION",
    })
    assert set({
        "facts",
        "formal_knowledge",
        "derived_result",
        "hypotheses",
        "unknowns",
        "evidence_refs",
        "validation_requirements",
        "next_action",
    }) <= set(view)
    assert view["evidence_refs"] == ["EVD-PROTOCOL"]
    assert view["next_action"].startswith("补齐缺失")


def test_359_selected_device_lifetime_requires_explicit_metric(monkeypatch):
    service = _FakeSkillService()
    monkeypatch.setattr(
        real_knowledge.RealKnowledgeAssessmentService,
        "current",
        classmethod(lambda cls: service),
    )
    monkeypatch.setattr(product_api, "device_slots", lambda device_id: _selected_device_detail())
    monkeypatch.setattr(
        product_api.KnowledgeReleaseConsumer,
        "current",
        classmethod(lambda cls: _FakeKnowledgeStatus()),
    )
    try:
        product_api.execute_device_skill("ssd-ctx", "storage-lifetime-budget", {})
    except ValueError as exc:
        assert str(exc) == "REQUESTED_METRIC_REQUIRED"
    else:
        raise AssertionError("lifetime execution must not guess a metric")
