from pathlib import Path
import json
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


def test_359_product_ui_exposes_four_layer_semantics():
    html = (Path(__file__).resolve().parents[1] / "storage_life" / "index.html").read_text(encoding="utf-8")
    assert "SEARCH_COVERAGE" in html
    assert "FACT_COVERAGE" in html
    assert "Datasheet Fact" in html
    assert "Runtime Observation" in html
    assert "Formal Knowledge" in html
    assert "Engineering Assessment" in html
    assert "诊断语义" in html


def test_359_selected_device_skill_route_is_exposed():
    from storage_life.app import app
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/product/devices/{device_id}/skills/{skill_id}/execute" in paths


def _load_359_real_source(name):
    path = Path(__file__).resolve().parent / "fixtures" / "storage_359_real_source_excerpts.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["sources"][name]


def test_359_timar_real_source_excerpt_resolves_direct_fact_candidates():
    source = _load_359_real_source("timar_97")
    pages = [(x["page"], x["text"], x["method"]) for x in source["pages"]]
    result = {
        "fields": [
            {
                "field_key": "capacity", "value": "256GB/512GB/1TB/2TB", "unit": "",
                "status": "found", "condition": "", "scope_type": "product_family",
                "scope_values": ["97 Series"], "confidence": 0.99,
                "evidence": {"page": 2, "quote": "Capacity 256GB/512GB/1TB/2TB"},
            },
            {
                "field_key": "interface", "value": "PCIe Gen4x4", "unit": "",
                "status": "found", "condition": "", "scope_type": "product_family",
                "scope_values": ["97 Series"], "confidence": 0.99,
                "evidence": {"page": 2, "quote": "PCIe Gen 4 16Gb/s interface with up to 4 lanes"},
            },
            {
                "field_key": "protocol", "value": "NVMe Revision 2.0", "unit": "",
                "status": "found", "condition": "", "scope_type": "product_family",
                "scope_values": ["97 Series"], "confidence": 0.99,
                "evidence": {"page": 2, "quote": "Compliant with NVMe Revision 2.0"},
            },
            {
                "field_key": "host_memory_buffer", "value": "supported", "unit": "",
                "status": "found", "condition": "", "scope_type": "product_family",
                "scope_values": ["97 Series"], "confidence": 0.99,
                "evidence": {"page": 2, "quote": "Supporting host memory buffer"},
            },
            {
                "field_key": "operating_temperature",
                "value": "A97 -40~85C; K97 -25~85C; S97 -10~70C", "unit": "",
                "status": "found", "condition": "family-specific", "scope_type": "product_family",
                "scope_values": ["A97M8", "K97M8", "S97M8"], "confidence": 0.99,
                "evidence": {"page": 2, "quote": "Operating: A97M8-Y/A97M8-PY -40°C~+85°C; K97M8-Y/K97M8-PY -25°C~+85°C; S97M8-Y/S97M8-PY -10°C~+70°C"},
            },
            {
                "field_key": "tbw", "value": "768/1500/3000/6000", "unit": "TB",
                "status": "found", "condition": "WAF=1", "scope_type": "capacity",
                "scope_values": ["256GB", "512GB", "1TB", "2TB"], "confidence": 0.99,
                "evidence": {"page": 2, "quote": "TBW: 256GB 768TB; 512GB 1500TB; 1TB 3000TB; 2TB 6000TB"},
            },
            {
                "field_key": "plp", "value": "Optional", "unit": "",
                "status": "found", "condition": "part-number dependent", "scope_type": "product_family",
                "scope_values": ["97 Series"], "confidence": 0.99,
                "evidence": {"page": 2, "quote": "Supporting PLP (Optional)"},
            },
            {
                "field_key": "nand_type", "value": "NAND Flash", "unit": "",
                "status": "found", "condition": "", "scope_type": "product_family",
                "scope_values": ["97 Series"], "confidence": 0.99,
                "evidence": {"page": 5, "quote": "Industrial SSDs use NAND Flash Memory"},
            },
        ]
    }
    expected = [
        "capacity", "interface", "protocol", "host_memory_buffer",
        "operating_temperature", "tbw", "plp", "nand_type",
    ]
    adapted = ai._adapt_single_pass(
        result, pages, "SSD", "TIMAR", "97 Series",
        source["source_id"], expected_fields=expected,
    )
    candidates = {x["canonical_name"]: x for x in adapted["candidates"]}
    assert set(candidates) == set(expected)
    assert candidates["tbw"]["condition"] == "WAF=1"
    assert "K97M8" in candidates["operating_temperature"]["scope"]
    assert candidates["nand_type"]["ai_value"] == "NAND Flash"


def test_359_timar_real_source_rejects_cell_type_without_explicit_cell_evidence():
    source = _load_359_real_source("timar_97")
    page5 = next(x for x in source["pages"] if x["page"] == 5)
    pages = [(page5["page"], page5["text"], page5["method"])]
    result = {
        "fields": [
            {
                "field_key": "nand_type", "value": "NAND Flash", "unit": "",
                "status": "found", "condition": "", "scope_type": "product_family",
                "scope_values": ["97 Series"], "confidence": 0.99,
                "evidence": {"page": 5, "quote": "Industrial SSDs use NAND Flash Memory"},
            },
            {
                "field_key": "cell_type", "value": "TLC", "unit": "",
                "status": "found", "condition": "", "scope_type": "product_family",
                "scope_values": ["97 Series"], "confidence": 0.99,
                "evidence": {"page": 5, "quote": "Industrial SSDs use NAND Flash Memory"},
            },
        ]
    }
    adapted = ai._adapt_single_pass(
        result, pages, "SSD", "TIMAR", "97 Series",
        source["source_id"], expected_fields=["nand_type", "cell_type"],
    )
    candidates = {x["canonical_name"]: x for x in adapted["candidates"]}
    facts = {x["field_key"]: x for x in adapted["facts"]}
    assert "nand_type" in candidates
    assert "cell_type" not in candidates
    assert facts["cell_type"]["status"] == "missing"
    assert facts["cell_type"]["semantic_rejection"] == "CELL_TYPE_REQUIRES_EXPLICIT_SLC_MLC_TLC_QLC_EVIDENCE"
    assert any(
        x.get("type") == "semantic_validation_failed" and x.get("field_key") == "cell_type"
        for x in adapted["review_queue"]
    )


def test_359_gd5f_real_source_excerpt_resolves_explicit_diagnostic_facts():
    source = _load_359_real_source("gd5f1gq5")
    pages = [(x["page"], x["text"], x["method"]) for x in source["pages"]]
    result = {
        "fields": [
            {
                "field_key": "pages_per_block", "value": "64", "unit": "pages",
                "status": "found", "condition": "", "scope_type": "product_family",
                "scope_values": ["GD5F1GQ5UExxG"], "confidence": 0.99,
                "evidence": {"page": 10, "quote": "1 block = (2K + 128) bytes x 64 pages"},
            },
            {
                "field_key": "operating_temperature", "value": "-40 to 85 / -40 to 105", "unit": "C",
                "status": "found", "condition": "part-number dependent", "scope_type": "product_family",
                "scope_values": ["GD5F1GQ5UExxG"], "confidence": 0.99,
                "evidence": {"page": 54, "quote": "Ambient Operating Temperature: -40 to 85°C / -40 to 105°C"},
            },
            {
                "field_key": "status_register", "value": "C0H", "unit": "",
                "status": "found", "condition": "", "scope_type": "product_family",
                "scope_values": ["GD5F1GQ5UExxG"], "confidence": 0.99,
                "evidence": {"page": 44, "quote": "Status C0H includes ECCS1 ECCS0 P_FAIL E_FAIL WEL OIP"},
            },
            {
                "field_key": "program_fail", "value": "P_FAIL", "unit": "",
                "status": "found", "condition": "program operation", "scope_type": "product_family",
                "scope_values": ["GD5F1GQ5UExxG"], "confidence": 0.99,
                "evidence": {"page": 44, "quote": "P_FAIL Program Fail indicates a program failure has occurred"},
            },
            {
                "field_key": "erase_fail", "value": "E_FAIL", "unit": "",
                "status": "found", "condition": "erase operation", "scope_type": "product_family",
                "scope_values": ["GD5F1GQ5UExxG"], "confidence": 0.99,
                "evidence": {"page": 44, "quote": "E_FAIL Erase Fail indicates an erase failure has occurred"},
            },
            {
                "field_key": "ecc_status", "value": "ECCS/ECCSE", "unit": "",
                "status": "found", "condition": "after valid READ", "scope_type": "product_family",
                "scope_values": ["GD5F1GQ5UExxG"], "confidence": 0.99,
                "evidence": {"page": 44, "quote": "ECCS/ECCSE provide ECC status after a valid READ"},
            },
        ]
    }
    expected = [
        "pages_per_block", "operating_temperature", "status_register",
        "program_fail", "erase_fail", "ecc_status",
    ]
    adapted = ai._adapt_single_pass(
        result, pages, "NAND Flash", "GigaDevice", "GD5F1GQ5UExxG",
        source["source_id"], expected_fields=expected,
    )
    candidates = {x["canonical_name"]: x for x in adapted["candidates"]}
    assert set(candidates) == set(expected)
    assert candidates["program_fail"]["ai_value"] == "P_FAIL"
    assert candidates["erase_fail"]["ai_value"] == "E_FAIL"
    assert candidates["status_register"]["ai_value"] == "C0H"
    assert candidates["pages_per_block"]["ai_value"] == "64"


def test_359_gd5f_bad_block_description_does_not_promote_to_count_observability():
    source = _load_359_real_source("gd5f1gq5")
    page48 = next(x for x in source["pages"] if x["page"] == 48)
    assert "Bad Block Mark" in page48["text"]
    fields = parameter_baseline.product_fields("NAND Flash", ai.expected_fields("NAND Flash"))
    by_name = {x["canonical_name"]: x for x in fields}
    assert "runtime_bad_block" in by_name
    assert "bad_block_observability" in by_name
    assert "runtime_bad_block" not in by_name["bad_block_observability"]["aliases"]
    assert "bad-block-count observability" in ai._semantic_rules("NAND Flash")
