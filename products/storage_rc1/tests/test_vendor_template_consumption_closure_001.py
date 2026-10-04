from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from storage_life import ai, product_api, templates
from storage_life.app import app
from storage_life.runtime_domain_strategy import EMMC_FIELD_ORDER


def test_t01_empty_or_unknown_vendor_preserves_generic_universe_for_every_device_type():
    for device_type in templates.device_types():
        generic = templates.analysis_fields_for(device_type)
        assert templates.effective_analysis_fields(device_type, "") == generic
        assert templates.effective_analysis_fields(device_type, "unknown vendor") == generic


def test_t02_nor_unknown_vendor_does_not_promote_generic_navigation_fields():
    generic = templates.analysis_fields_for("NOR Flash")
    effective = templates.effective_analysis_fields("NOR Flash", "unknown vendor")
    navigation_only = {"capacity", "interface", "voltage", "clock_frequency"}

    assert effective == generic
    assert navigation_only.isdisjoint(set(effective) - set(generic))


def test_t03_timar_ssd_adds_only_existing_vendor_override_fields():
    generic = templates.analysis_fields_for("SSD")
    effective = templates.effective_analysis_fields("SSD", "TIMAR")
    vendor_only = ["sequential_read", "sequential_write", "mtbf", "uber"]

    assert effective == generic + vendor_only
    assert effective[: len(generic)] == generic
    assert all(effective.count(key) == 1 for key in effective)
    assert [key for key in effective if key not in generic] == vendor_only
    assert set(effective) <= set(templates.fields_for("SSD"))


def test_t04_vendor_schema_and_expected_fields_include_vendor_only_fields():
    _, ssd_order = ai._single_pass_schema("SSD", "TIMAR")
    expected_ssd = {item["canonical_name"] for item in ai.expected_fields("SSD", "TIMAR")}
    assert {"sequential_read", "sequential_write", "mtbf", "uber"} <= set(ssd_order)
    assert {"sequential_read", "sequential_write", "mtbf", "uber"} <= expected_ssd


def test_t05_emmc_runtime_schema_retains_all_37_fields_in_original_order(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    _schema, emmc_order = ai._single_pass_schema("eMMC", "SkyHigh")
    assert emmc_order == list(EMMC_FIELD_ORDER)
    assert len(emmc_order) == 37


def test_t03_formal_extraction_schema_contains_every_existing_vendor_read_plan_target(monkeypatch):
    pages = [(1, "PRODUCT LINE-UP\nTIMAR SSD", "text")]
    read_plan = templates.build_read_plan(pages, "SSD", "TIMAR")
    schema_capture = {}

    def fake_extraction(_instructions, _payload, schema, **_kwargs):
        schema_capture["schema"] = schema
        keys = schema["properties"]["fields"]["items"]["properties"]["field_key"]["enum"]
        return {"fields": [
            {
                "field_key": key,
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
            }
            for key in keys
        ]}, None, 1

    monkeypatch.setattr(ai, "_primary_or_secondary_extraction", fake_extraction)
    monkeypatch.setattr(ai, "_run_critical_targeted_supplement", lambda base, *_args, **_kwargs: base)
    ai.extract_specification_bundle_once(
        [{"source_id": "timar-source", "pages": pages}],
        "SSD",
        "TIMAR",
        "TIMAR test family",
    )
    schema_fields = set(
        schema_capture["schema"]["properties"]["fields"]["items"]["properties"]["field_key"]["enum"]
    )
    read_plan_targets = {key for entry in read_plan for key in entry["target_fields"]}
    assert read_plan_targets <= schema_fields
    assert not (read_plan_targets - schema_fields)


def test_t04_vendor_only_fields_reach_selected_device_slots_and_review(monkeypatch):
    device = {"id": "timar-ssd", "vendor": "TIMAR", "model": "T97", "device_type": "SSD"}
    keys = ["sequential_read", "sequential_write", "mtbf", "uber"]
    coverage = {key: {"state": "FOUND", "reason": "searched"} for key in keys}
    candidates = {
        key: [{"id": f"candidate-{key}", "canonical_name": key, "verify_status": "pending",
               "ai_value": "test", "ai_unit": "", "evidence": [{"source_id": "source"}]}]
        for key in keys
    }
    seen_vendors = []
    original_expected_fields = ai.expected_fields

    def expected_fields(device_type, vendor=""):
        seen_vendors.append(vendor)
        return original_expected_fields(device_type, vendor)

    monkeypatch.setattr(ai, "expected_fields", expected_fields)
    monkeypatch.setattr(product_api.core, "list_devices", lambda: [device])
    monkeypatch.setattr(product_api, "_coverage_states", lambda _device_id: coverage)
    monkeypatch.setattr(product_api, "_candidate_map", lambda _device_id: candidates)
    monkeypatch.setattr(product_api, "_enrich_evidence", lambda _device, evidence: evidence)
    monkeypatch.setattr(product_api, "_formal_knowledge", lambda *_args, **_kwargs: {"status": "NO_MATCH"})
    monkeypatch.setattr(product_api, "_diagnostic_semantics", lambda **_kwargs: {})
    monkeypatch.setattr(product_api.core, "specification_workflow_status", lambda _device_id: {"formal_ready": False})
    monkeypatch.setattr(product_api, "_device_lifecycle", lambda _device_id: {"formal_ready": False})
    monkeypatch.setattr(product_api.core, "get_device_conclusion", lambda _device_id: {})
    monkeypatch.setattr(product_api.core, "list_candidate_review_history", lambda _candidate_id: [])

    slots = product_api.device_slots("timar-ssd")["slots"]
    rows = product_api.review_workbench("timar-ssd")["rows"]
    assert set(keys) <= {item["canonical_name"] for item in slots}
    assert set(keys) <= {item["canonical_name"] for item in rows}
    assert all(next(item for item in slots if item["canonical_name"] == key)["candidate_id"] for key in keys)
    assert all(next(item for item in rows if item["canonical_name"] == key)["candidate_id"] for key in keys)
    assert all(next(item for item in slots if item["canonical_name"] == key)["evidence"] for key in keys)
    assert all(next(item for item in rows if item["canonical_name"] == key)["evidence"] for key in keys)
    assert seen_vendors == ["TIMAR", "TIMAR"]


def test_t05_identity_headings_prioritize_pages_without_extracting_identity(monkeypatch):
    headings = templates.identity_headings_for("TIMAR")
    assert "PRODUCT SPECIFICATION" in headings
    pages = [(1, "cover", "text"), (7, "PRODUCT SPECIFICATION details", "text"), (10, "appendix", "text")]
    assert templates.identity_page_hits(pages, "TIMAR") == [7]

    monkeypatch.setattr(templates, "build_read_plan", lambda *_args, **_kwargs: [])
    long_pages = [(n, ("PRODUCT SPECIFICATION\n" if n == 7 else "body\n") + ("x" * 9000), "text") for n in range(1, 11)]
    selected = ai._single_pass_pages(long_pages, "SSD", "TIMAR", max_chars=8000)
    assert selected[0][0] == 7
    assert templates.template_summary("SSD", "TIMAR")["identity_headings"] == headings


def test_t08_unknown_is_read_only_and_not_a_manual_completion_blocker(monkeypatch):
    device = {"id": "review-device", "vendor": "TIMAR", "model": "T97", "device_type": "SSD"}
    fields = [
        {"canonical_name": "unknown_key", "parameter_name": "Unknown", "group": "KEY_SPEC", "requirement_level": "MUST"},
        {"canonical_name": "attention_key", "parameter_name": "Attention", "group": "KEY_SPEC", "requirement_level": "MUST"},
    ]
    workflow = {"formal_ready": False, "status": "pending_confirmation"}
    monkeypatch.setattr(product_api.core, "list_devices", lambda: [device])
    monkeypatch.setattr(product_api.parameter_baseline, "product_fields", lambda *_args, **_kwargs: fields)
    monkeypatch.setattr(product_api, "_coverage_states", lambda _device_id: {
        "unknown_key": {"state": "NOT_SPECIFIED", "reason": "not found"},
        "attention_key": {"state": "FOUND", "reason": "candidate unresolved"},
    })
    monkeypatch.setattr(product_api, "_candidate_map", lambda _device_id: {
        "attention_key": [{"id": "attention-candidate", "verify_status": "pending", "ai_value": "x", "evidence": []}]
    })
    monkeypatch.setattr(product_api.core, "list_candidate_review_history", lambda _candidate_id: [])
    monkeypatch.setattr(product_api.core, "specification_workflow_status", lambda _device_id: workflow)

    workbench = product_api.review_workbench("review-device")
    result = product_api.complete_parameter_review("review-device")
    assert workbench["ux_summary"]["needs_attention"] == 1
    assert workbench["ux_summary"]["unknown"] == 1
    assert workbench["ux_summary"]["action_required_count"] == 1
    assert result["completed"] is False
    assert [item["ux_state"] for item in result["blockers"]] == ["NEEDS_ATTENTION"]
    assert [item["ux_state"] for item in result["non_actionable_missing"]] == ["UNKNOWN"]
    assert result["workflow"]["formal_ready"] is False
    assert result["workflow"]["non_actionable_missing"] == result["non_actionable_missing"]


def test_t06_t07_t08_import_gates_rendering_and_review_ui_contracts():
    html = (Path(__file__).parents[1] / "storage_life" / "index.html").read_text(encoding="utf-8")
    assert '<option value="">请选择器件类型</option>' in html
    assert "identityTypeSelect.value=''" in html
    assert "if(!supportedTypes.includes(deviceType))" in html
    assert "if(!supportedTypes.includes(f.elements.device_type.value))" in html
    assert "displayValue=v=>" in html
    assert "esc=v=>String(displayValue(v)??'')" in html
    assert "manualActionRows=rows.filter(x=>x.ux_state==='NEEDS_ATTENTION')" in html
    assert "规格书未声明 / 无法确定（${unknownRows.length}项）" in html
    assert "action_required_count" in html
    rendered_review = html[html.index("renderReviewWorkbench=function"):].splitlines()[0]
    assert "exception_count" not in rendered_review


def test_t09_complete_review_api_409_explains_manual_blockers(monkeypatch):
    monkeypatch.setattr(product_api, "complete_parameter_review", lambda _device_id: {
        "completed": False,
        "blockers": [{"canonical_name": "life_time_a", "ux_state": "NEEDS_ATTENTION"}],
        "non_actionable_missing": [{"canonical_name": "pre_eol", "ux_state": "UNKNOWN"}],
        "workflow": {"formal_ready": False},
    })

    response = TestClient(app).post("/api/product/devices/d/review/complete")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "需要人工处理或确认" in detail["message"]
    assert detail["blockers"][0]["ux_state"] == "NEEDS_ATTENTION"
    assert detail["non_actionable_missing"][0]["ux_state"] == "UNKNOWN"


def test_t10_complete_review_api_unknown_only_explains_gate_without_manual_review(monkeypatch):
    unknown = [{"canonical_name": "pre_eol", "ux_state": "UNKNOWN"}]
    monkeypatch.setattr(product_api, "complete_parameter_review", lambda _device_id: {
        "completed": False,
        "blockers": [],
        "non_actionable_missing": unknown,
        "workflow": {"formal_ready": False, "non_actionable_missing": unknown},
    })

    response = TestClient(app).post("/api/product/devices/d/review/complete")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "门禁尚未满足" in detail["message"]
    assert "不要求逐项人工判断" in detail["message"]
    assert detail["blockers"] == []
    assert detail["non_actionable_missing"] == unknown
    assert detail["workflow"]["formal_ready"] is False


def test_t11_complete_review_api_formal_ready_returns_200_unchanged(monkeypatch):
    ready = {
        "device_id": "d",
        "completed": True,
        "blockers": [],
        "non_actionable_missing": [],
        "workflow": {"formal_ready": True},
        "gate": "CONFIRMED_DEVICE_FACT_GATE_UNCHANGED",
    }
    monkeypatch.setattr(product_api, "complete_parameter_review", lambda _device_id: ready)

    response = TestClient(app).post("/api/product/devices/d/review/complete")

    assert response.status_code == 200
    assert response.json() == ready
