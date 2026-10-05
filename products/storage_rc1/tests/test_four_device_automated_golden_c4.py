from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from storage_life import ai, parameter_baseline, product_api, templates
from storage_life import runtime_bridge
from storage_life.runtime_domain_strategy import EMMC_FIELD_ORDER


FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN_PATH = FIXTURES / "storage_four_device_automated_golden_c4.json"
EXCERPT_PATH = FIXTURES / "storage_four_device_source_excerpts_c4.json"
REGISTRY_PATH = FIXTURES / "storage_four_device_historical_registry_359.json"
FROZEN_SOURCE_SHA256 = {
    "G-S40FC016": "a40b2ecc44c89184dbcaed3f8d48e7615e4daefaf7732076a40f7bc73751e978",
    "G-GD5F1GQ5": "99e7833a64c2bf9b85272218e66b911ca33fb3504e89af539104edf617cf6c1a",
    "G-GD25Q64E": "1330342a7dd6d123bde0486fb2381a359f6a8890a647d4f7d2a282a963938602",
    "G-TIMAR-97": "7242c29244f17e25ce31330dd6b97412a64e193c28e2e18599f759679dac5e1a",
    "G-TIMAR-97-HTML": "dbc95e45dfcea8694f17d2e1951d7a95647a139d0ed104a4bde5edc10e2ba29c",
}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _golden_cases():
    return _load(GOLDEN_PATH)["cases"]


def _excerpts_by_source():
    grouped = defaultdict(list)
    for item in _load(EXCERPT_PATH)["excerpts"]:
        grouped[item["source_id"]].append(item)
    return grouped


def _source_pages(source_ids):
    excerpts = _excerpts_by_source()
    by_source = {}
    for source_id in source_ids:
        pages = defaultdict(list)
        for item in excerpts[source_id]:
            pages[int(item["page"])].append(item["text"])
        by_source[source_id] = [
            (page, "\n".join(texts), "frozen_source_excerpt")
            for page, texts in sorted(pages.items())
        ]
    return by_source


def _assertions_for_sources(case, source_ids):
    allowed = set(source_ids)
    return [item for item in case["positive_assertions"] if item["source_id"] in allowed]


def build_contract_response(case, current_schema, source_ids):
    """Deterministic Provider stand-in: Golden data remains in the frozen fixture."""
    expected = current_schema["properties"]["fields"]["items"]["properties"]["field_key"]["enum"]
    assertions = _assertions_for_sources(case, source_ids)
    by_key = {item["field_key"]: item for item in assertions}
    assert len(by_key) == len(assertions), f"duplicate positive Golden field in {case['golden_id']}"
    fields = []
    for field_key in expected:
        assertion = by_key.get(field_key)
        if assertion is None:
            fields.append({
                "field_key": field_key,
                "value": None,
                "unit": None,
                "condition": None,
                "scope_type": "product_family",
                "scope_values": [],
                "evidence": None,
                "conflict_evidence": [],
                "confidence": 0,
                "status": "missing",
                "derived": False,
                "knowledge_type": "specification",
            })
            continue
        fields.append({
            "field_key": field_key,
            "value": assertion["value"],
            "unit": assertion.get("unit"),
            "condition": assertion.get("condition"),
            "scope_type": assertion.get("scope_type", "product_family"),
            "scope_values": assertion.get("scope_values", []),
            "evidence": {
                "source_id": assertion["source_id"],
                "page": assertion["page"],
                "section": assertion["section"],
                "quote": assertion["quote"],
            },
            "conflict_evidence": [],
            "confidence": 1,
            "status": "found",
            "derived": False,
            "knowledge_type": "specification",
        })
    return {"fields": fields}


def _case_modes(case):
    if case["golden_id"] == "G-TIMAR-97":
        refs = case["source_refs"]
        return [
            ("pdf_only", [refs[0]["source_id"]]),
            ("pdf_and_html", [item["source_id"] for item in refs]),
        ]
    return [("single_source", [item["source_id"] for item in case["source_refs"]])]


def test_t01_frozen_registry_source_identity_and_legacy_fixture_boundary():
    golden = _load(GOLDEN_PATH)
    registry = _load(REGISTRY_PATH)
    refs = {ref["source_id"]: ref for case in golden["cases"] for ref in case["source_refs"]}
    historical = {item["source_id_sha256"]: item for item in registry["sources"]}
    historical.update({
        item["companion_snapshot"]["source_id_sha256"]: {
            **item["companion_snapshot"], "source_id_sha256": item["companion_snapshot"]["source_id_sha256"]
        }
        for item in registry["sources"] if item.get("companion_snapshot")
    })

    assert golden["baseline"] == "integration/storage@fe4846b2183f2ff5349d201de842377afb81dd87"
    assert len(golden["cases"]) == 4
    assert {case["golden_id"] for case in golden["cases"]} == {
        "G-S40FC016", "G-GD5F1GQ5", "G-GD25Q64E", "G-TIMAR-97"
    }
    assert set(refs) == set(FROZEN_SOURCE_SHA256.values())
    for source_id, ref in refs.items():
        assert ref["sha256"] == source_id == FROZEN_SOURCE_SHA256[
            "G-TIMAR-97-HTML" if ref["source_kind"] == "official_html_snapshot"
            else next(gid for gid, sha in FROZEN_SOURCE_SHA256.items() if sha == source_id)
        ]
        assert historical[source_id]["source_id_sha256"] == ref["sha256"]
        assert historical[source_id]["drive_id"] == ref["drive_id"]
        assert historical[source_id]["file_name"] == ref["file_name"]

    excerpts = _load(EXCERPT_PATH)["excerpts"]
    assert {item["source_id"] for item in excerpts} <= set(refs)
    assert all(item["page"] and item["section"] and item["method"] and item["text"] for item in excerpts)

    timar_refs = next(case["source_refs"] for case in golden["cases"] if case["golden_id"] == "G-TIMAR-97")
    assert timar_refs[0]["source_id"] != timar_refs[1]["source_id"]
    legacy_fixtures = {
        item["regression_fixture"]: item["fixture_role"]
        for item in registry["sources"]
        if item.get("regression_fixture")
    }
    expected_legacy_fixtures = {
        "test_assets/storage_rc1/fixtures/M03_PARAMETER_GOLDEN_EMMC.json",
        "test_assets/storage_rc1/fixtures/M23_PARAMETER_GOLDEN_SSD_NVME.json",
        "test_assets/storage_rc1/fixtures/M24_PARAMETER_GOLDEN_RAW_NAND.json",
        "test_assets/storage_rc1/fixtures/M25_PARAMETER_GOLDEN_NOR.json",
    }
    assert set(legacy_fixtures) == expected_legacy_fixtures
    assert len(legacy_fixtures) == 4
    assert set(legacy_fixtures.values()) == {"TEST_ONLY"}
    golden_and_excerpts = json.dumps([golden, _load(EXCERPT_PATH)], ensure_ascii=False)
    assert all(path not in golden_and_excerpts for path in expected_legacy_fixtures)
    assert golden["legacy_regression_fixtures_are_not_device_golden"] is True


def test_t02_legacy_fixtures_remain_distinct_from_real_device_goldens():
    fixtures = Path(__file__).parents[3] / "test_assets" / "storage_rc1" / "fixtures"
    emmc = _load(fixtures / "M03_PARAMETER_GOLDEN_EMMC.json")
    nand = _load(fixtures / "M24_PARAMETER_GOLDEN_RAW_NAND.json")
    nor = _load(fixtures / "M25_PARAMETER_GOLDEN_NOR.json")
    ssd = _load(fixtures / "M23_PARAMETER_GOLDEN_SSD_NVME.json")
    real = {case["golden_id"]: case for case in _golden_cases()}

    def parameter(payload, key):
        return next(item for item in payload["parameters"] if item["parameter_id"] == key)

    assert parameter(emmc["payload"], "capacity")["value"] == 64
    assert next(x for x in real["G-S40FC016"]["positive_assertions"] if x["field_key"] == "capacity")["value"] == "16"
    assert parameter(nand["payload"], "nand_type")["value"] == "TLC"
    assert parameter(nand["payload"], "pe_cycles")["value"] == 3000
    assert nor["mock_id"] == "M25"
    assert nor["purpose"] == "PARAMETER_GOLDEN_NOR"
    assert next(x for x in real["G-GD5F1GQ5"]["positive_assertions"] if x["field_key"] == "cell_type")["value"] == "SLC"
    assert next(x for x in real["G-GD5F1GQ5"]["positive_assertions"] if x["field_key"] == "pe_cycles")["value"] == "100000"
    assert parameter(ssd["payload"], "tbw")["value"] == 600
    assert next(x for x in real["G-TIMAR-97"]["positive_assertions"] if x["field_key"] == "tbw")["value"] == "768/1500/3000/6000"


@pytest.mark.parametrize(("golden_id", "field_key", "required_labels"), [
    ("G-GD5F1GQ5", "ecc_status", {"no_error", "corrected_1_bit", "corrected_2_bit", "corrected_3_bit", "corrected_4_bit", "uncorrectable"}),
    ("G-GD25Q64E", "status_register", {"wel", "wip", "sus1", "sus2"}),
    ("G-S40FC016", "emmc_version", {"emmc_5_1", "jesd84_b51"}),
])
def test_t02b_compound_golden_assertions_have_semantically_complete_source_evidence(
    golden_id, field_key, required_labels
):
    case = next(item for item in _golden_cases() if item["golden_id"] == golden_id)
    assertion = next(item for item in case["positive_assertions"] if item["field_key"] == field_key)
    excerpts = _excerpts_by_source()
    evidence = assertion["supporting_evidence"]

    assert {item["claim"] for item in evidence} == required_labels
    for item in evidence:
        matching_excerpt = next(
            excerpt for excerpt in excerpts[item["source_id"]]
            if int(excerpt["page"]) == item["page"]
        )
        assert item["section"] == matching_excerpt["section"]
        assert item["quote"] in matching_excerpt["text"]

    primary_excerpt = next(
        excerpt for excerpt in excerpts[assertion["source_id"]]
        if int(excerpt["page"]) == assertion["page"]
    )
    assert assertion["quote"] in primary_excerpt["text"]


@pytest.mark.parametrize("golden_id", ["G-S40FC016", "G-GD5F1GQ5", "G-GD25Q64E", "G-TIMAR-97"])
def test_t03_to_t07_deterministic_contract_adapter_evidence_coverage_and_review(golden_id, monkeypatch):
    case = next(item for item in _golden_cases() if item["golden_id"] == golden_id)
    provider_boundary_calls = []
    if case["device_type"] == "eMMC":
        monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    else:
        monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "legacy")

    def forbidden_provider(*_args, **_kwargs):
        pytest.fail("C4 must not call a real model/provider/network path")

    monkeypatch.setattr(ai, "_call", forbidden_provider)
    monkeypatch.setattr(ai, "_call_direct", forbidden_provider)
    monkeypatch.setattr(runtime_bridge, "call_json", forbidden_provider)
    monkeypatch.setattr(runtime_bridge, "call_parameter_extract", forbidden_provider)
    monkeypatch.setattr(ai, "_run_critical_targeted_supplement", lambda base, *_a, **_kw: base)

    for mode, source_ids in _case_modes(case):
        source_pages = _source_pages(source_ids)
        sources = [{"source_id": source_id, "pages": pages} for source_id, pages in source_pages.items()]
        calls = []

        def deterministic_stub(_instructions, payload, schema, **_kwargs):
            calls.append(payload)
            provider_boundary_calls.append(case["golden_id"])
            response = build_contract_response(case, schema, source_ids)
            return response, {"status": "deterministic_stub"}, 0

        monkeypatch.setattr(ai, "_primary_or_secondary_extraction", deterministic_stub)
        result = ai.extract_specification_bundle_once(
            sources, case["device_type"], case["vendor"], case["sample"]
        )

        expected_schema, expected_order = ai._single_pass_schema(case["device_type"], case["vendor"])
        actual_order = expected_schema["properties"]["fields"]["items"]["properties"]["field_key"]["enum"]
        assert actual_order == expected_order
        if golden_id == "G-TIMAR-97":
            assert {"sequential_read", "sequential_write", "mtbf", "uber"} <= set(expected_order)
        assert result["schema_valid"] is True
        assert result["unresolved_evidence"] == []
        facts = {item["field_key"]: item for item in result["facts"]}
        selected_positive = _assertions_for_sources(case, source_ids)
        for assertion in selected_positive:
            fact = facts[assertion["field_key"]]
            assert fact["status"] == "found", (golden_id, mode, assertion["field_key"], fact)
            assert fact["value"] == assertion["value"]
            assert fact["condition"] == assertion.get("condition")
            assert fact["scope_type"] == assertion.get("scope_type", "product_family")
            assert fact["scope_values"] == assertion.get("scope_values", [])
            resolved = fact["resolved_evidence"]
            assert resolved and resolved["source_id"] == assertion["source_id"]
            assert resolved["page"] == assertion["page"]
            assert assertion["quote"].casefold() in resolved["quote"].casefold()

        expected_found = {item["field_key"] for item in selected_positive}
        actual_found = {key for key, fact in facts.items() if fact["status"] == "found"}
        assert actual_found == expected_found, (golden_id, mode, actual_found - expected_found)
        for assertion in case["negative_assertions"]:
            if assertion.get("source_mode") and assertion["source_mode"] != mode:
                continue
            fact = facts[assertion["field_key"]]
            assert fact["status"] == assertion.get("status", "missing")
            assert fact["value"] is None
            assert fact["resolved_evidence"] is None

        coverage = {item["field_key"]: item for item in result["coverage"]["states"]}
        for assertion in selected_positive:
            assert coverage[assertion["field_key"]]["state"] == "FOUND"
        for assertion in case["condition_assertions"]:
            if assertion["field_key"] in expected_found:
                assert facts[assertion["field_key"]]["condition"] == assertion["condition"]
        for assertion in case["scope_assertions"]:
            if "rule" not in assertion and assertion["field_key"] in expected_found:
                fact = facts[assertion["field_key"]]
                assert fact["scope_type"] == assertion["scope_type"]
                if "scope_values" in assertion:
                    assert fact["scope_values"] == assertion["scope_values"]

        for binding in case["source_binding_assertions"]:
            if binding["source_mode"] == mode:
                assert facts[binding["field_key"]]["resolved_evidence"]["source_id"] == binding["source_id"]

        if golden_id == "G-TIMAR-97" and mode == "pdf_only":
            assert facts["cell_type"]["status"] == "missing"
            assert facts["cell_type"]["resolved_evidence"] is None
        if golden_id == "G-TIMAR-97" and mode == "pdf_and_html":
            assert facts["cell_type"]["value"] == "TLC"
            assert facts["cell_type"]["resolved_evidence"]["source_id"] == FROZEN_SOURCE_SHA256["G-TIMAR-97-HTML"]
            assert facts["smart_health"]["resolved_evidence"]["source_id"] == FROZEN_SOURCE_SHA256["G-TIMAR-97-HTML"]
        if golden_id == "G-S40FC016":
            assert expected_order == list(EMMC_FIELD_ORDER)
            assert len(result["facts"]) == 37
            assert [item["field_key"] for item in result["facts"]] == list(EMMC_FIELD_ORDER)

        # Golden missing is a valid explicit state; it does not receive fabricated value/evidence.
        for assertion in case["negative_assertions"]:
            if assertion.get("source_mode") and assertion["source_mode"] != mode:
                continue
            missing_fact = facts[assertion["field_key"]]
            assert missing_fact["status"] == "missing"
            assert missing_fact["value"] is None and not missing_fact["evidence"]

        assert result["model_calls"] == 0
        assert result["review_gate"]["status"] in {"required", "not_required"}
        assert len(calls) == 1

    assert len(provider_boundary_calls) == (2 if golden_id == "G-TIMAR-97" else 1)


@pytest.mark.parametrize("golden_id", ["G-S40FC016", "G-GD5F1GQ5", "G-GD25Q64E", "G-TIMAR-97"])
def test_t10_product_slots_and_review_workbench_surface_vendor_schema_without_confirming(golden_id, monkeypatch):
    case = next(item for item in _golden_cases() if item["golden_id"] == golden_id)
    device_id = f"c4-{golden_id}"
    device = {"id": device_id, "device_type": case["device_type"], "vendor": case["vendor"], "model": case["sample"]}
    schema_fields = ai.expected_fields(case["device_type"], case["vendor"])
    expected = parameter_baseline.product_fields(case["device_type"], schema_fields)
    keys = [item["canonical_name"] for item in expected]
    found_key = keys[0]
    evidence = [{"source_id": case["source_refs"][0]["source_id"], "source_page": 1, "source_text": "frozen deterministic evidence"}]
    candidate = {
        "id": f"candidate-{golden_id}", "canonical_name": found_key, "verify_status": "pending",
        "ai_value": "frozen fact", "ai_unit": "", "final_value": None, "final_unit": "",
        "condition": "", "scope": "", "evidence": evidence,
    }
    monkeypatch.setattr(product_api.core, "list_devices", lambda: [device])
    monkeypatch.setattr(product_api, "_coverage_states", lambda _id: {
        key: {"state": "FOUND" if key == found_key else "NOT_SPECIFIED", "reason": "C4 deterministic"}
        for key in keys
    })
    monkeypatch.setattr(product_api, "_candidate_map", lambda _id: {found_key: [candidate]})
    monkeypatch.setattr(product_api, "_enrich_evidence", lambda _device, rows: rows)
    monkeypatch.setattr(product_api, "_formal_knowledge", lambda *_a, **_kw: {"status": "NO_MATCH"})
    monkeypatch.setattr(product_api, "_diagnostic_semantics", lambda **_kw: {})
    monkeypatch.setattr(product_api.core, "specification_workflow_status", lambda _id: {"formal_ready": False, "status": "pending_confirmation"})
    monkeypatch.setattr(product_api, "_device_lifecycle", lambda _id: {"formal_ready": False, "status": "pending_confirmation"})
    monkeypatch.setattr(product_api.core, "get_device_conclusion", lambda _id: {})
    monkeypatch.setattr(product_api.core, "list_candidate_review_history", lambda _id: [])

    slots_response = product_api.device_slots(device_id)
    workbench = product_api.review_workbench(device_id)
    slots = {row["canonical_name"]: row for row in slots_response["slots"]}
    rows = {row["canonical_name"]: row for row in workbench["rows"]}

    assert set(keys) <= set(slots)
    assert set(keys) <= set(rows)
    assert slots[found_key]["status"] == "UNREVIEWED"
    assert slots[found_key]["evidence"]
    assert rows[found_key]["ux_state"] == "TRUSTED"
    assert rows[found_key]["evidence"]
    assert all(slots[key]["status"] == "NOT_FOUND" for key in keys if key != found_key)
    assert all(rows[key]["ux_state"] == "UNKNOWN" for key in keys if key != found_key)
    assert workbench["ux_summary"]["action_required_count"] == 0
    assert slots_response["device_facts"] == []
    assert slots_response["workflow"]["formal_ready"] is False
    assert slots_response["lifecycle"]["formal_ready"] is False

    if golden_id == "G-TIMAR-97":
        vendor_fields = {"sequential_read", "sequential_write", "mtbf", "uber"}
        assert vendor_fields <= set(keys)
        assert vendor_fields <= set(slots)
        assert vendor_fields <= set(rows)


def test_t11_and_t12_no_real_provider_calls_or_out_of_scope_changes():
    repo_root = Path(__file__).parents[3]
    changed = [
        line[3:].strip()
        for line in __import__("subprocess").run(
            ["git", "status", "--short"], cwd=repo_root,
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    ]
    allowed = {
        "products/storage_rc1/tests/test_four_device_automated_golden_c4.py",
        "products/storage_rc1/tests/fixtures/storage_four_device_automated_golden_c4.json",
        "products/storage_rc1/tests/fixtures/storage_four_device_source_excerpts_c4.json",
    }
    assert set(changed) <= allowed
    assert all((repo_root / path).is_file() for path in allowed)
    assert "products/storage_rc1/storage_life" not in "\n".join(changed)
    assert "runtime/" not in "\n".join(changed)
    assert all(not path.startswith("test_assets/storage_rc1/fixtures/M0") for path in changed)
