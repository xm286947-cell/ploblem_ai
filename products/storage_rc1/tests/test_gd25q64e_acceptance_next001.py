import json
from pathlib import Path

from fastapi.testclient import TestClient

from storage_life import ai, core
from storage_life.app import app

FIXTURE = Path(__file__).parent / "fixtures" / "gd25q64e_rev16_excerpt.json"


def _contract(found):
    _, keys = ai._single_pass_schema("NOR Flash")
    fields = []
    for key in keys:
        item = {
            "field_key": key, "value": None, "unit": None, "condition": None,
            "scope_type": "product_family", "scope_values": [], "evidence": None,
            "conflict_evidence": [], "confidence": 0, "status": "missing", "derived": False,
            "knowledge_type": "specification",
        }
        item.update(found.get(key) or {})
        fields.append(item)
    return {"fields": fields}


def _found(value, page, section, quote, *, unit=None, scope_values=None):
    return {
        "value": value, "unit": unit, "status": "found", "confidence": .99,
        "scope_type": "part_number" if scope_values else "product_family",
        "scope_values": scope_values or [],
        "evidence": {"source_id": "pdf", "page": page, "section": section, "quote": quote},
    }


def _analysis_with_one_real_supplement(monkeypatch):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pages = [tuple(x) for x in fixture["pages"]]
    parts = ["GD25Q64EXEIG", "GD25Q64ENIG", "GD25Q64EQIG", "GD25Q64ETIG", "GD25Q64EBIG", "GD25Q64EFIG"]
    initial = _contract({
        "manufacturer": _found("GigaDevice Semiconductor Inc.", 66, "Important Notice", "GigaDevice Semiconductor Inc."),
        "product_family": _found("GD25Q64E", 4, "FEATURES", "64M-bit Serial Flash"),
        "covered_part_numbers": _found("Valid Part Numbers", 52, "Valid Part Numbers", "GD25Q64EXEIG", scope_values=parts),
        "revision": _found("1.6", 65, "REVISION HISTORY", "1.6 Add VPWD and tPWD"),
        "revision_date": _found("2024-04-28", 65, "REVISION HISTORY", "2024-4-28"),
        "pe_cycles": _found("100000", 4, "FEATURES", "Minimum 100,000 Program/Erase Cycles", unit="cycles"),
        # Force the first pass to be unresolved: the locator points outside the supplied/searchable pages.
        "retention": _found("20", 999, "FEATURES", "20-year data retention typical", unit="years"),
    })
    supplement = {"fields": [{
        "field_key": "retention", "value": "20", "unit": "years", "condition": None,
        "scope_type": "product_family", "scope_values": [],
        "evidence": {"source_id": "pdf", "page": 4, "section": "FEATURES", "quote": "20-year data retention typical"},
        "conflict_evidence": [], "confidence": .99, "status": "found", "derived": False,
        "knowledge_type": "specification",
    }]}
    calls = []

    def fake_call(instructions, payload, schema, client=None):
        calls.append(payload)
        return initial if len(calls) == 1 else supplement

    monkeypatch.setattr(ai, "_call", fake_call)
    result = ai.extract_specification_bundle_once(
        [{"source_id": "pdf", "pages": pages}], "NOR Flash", "GigaDevice", "GD25Q64E"
    )
    return result, calls


def _persist_acceptance_state(tmp_path, monkeypatch, result):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "gd25q64e-acceptance.sqlite3")
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ("pdf", "GD25Q64E.pdf", "sha", str(tmp_path / "GD25Q64E.pdf"), "", "GigaDevice", 66, "now"))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ("d", "GigaDevice", "GD25Q64E", "NOR Flash", "pdf"))
        for index, model in enumerate(result["models"]):
            con.execute("""INSERT INTO document_models
              (id,source_id,device_id,ai_model,scope,source_page,source_text,confidence)
              VALUES (?,?,?,?,?,?,?,?)""",
              (f"m{index}", "pdf", "d", model["value"], model.get("scope", ""), model.get("page", 52), model.get("quote", ""), model.get("confidence", .99)))
        con.execute("""INSERT INTO extraction_runs
          (id,device_id,source_id,extraction_mode,schema_valid,model_calls,unresolved_evidence_json,review_required,review_queue_json,
           facts_json,expected_fields_json,searched_pages_json,searched_sections_json,searched_fields_json,coverage_json,
           coverage_layers_json,document_analysis_json,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ("r", "d", "pdf", "agent_single_pass_generic", 1, result["model_calls"],
           json.dumps(result.get("unresolved_evidence") or []), 1 if result["review_required"] else 0,
           json.dumps(result.get("review_queue") or []), json.dumps(result["facts"]), json.dumps(result["expected_fields"]),
           json.dumps(result["searched_pages"]), json.dumps(result["searched_sections"]), json.dumps(result["searched_fields"]),
           json.dumps(result["coverage"]), json.dumps(result["coverage_layers"]), json.dumps(result["document_analysis"]), "now"))


def test_gd25q64e_document_analysis_to_supplement_coverage_review_and_ui(tmp_path, monkeypatch):
    result, calls = _analysis_with_one_real_supplement(monkeypatch)
    assert len(calls) == 2
    assert calls[1]["operation"] == "critical_unresolved_targeted_supplement"
    assert calls[1]["target_fields"] == ["retention"]
    assert result["supplement_rounds"] == 1
    assert result["coverage"]["critical_unresolved"] == []
    assert result["review_gate"]["status"] == "not_required"

    _persist_acceptance_state(tmp_path, monkeypatch, result)
    client = TestClient(app)

    analysis = client.get("/api/devices/d/document-analysis")
    assert analysis.status_code == 200
    body = analysis.json()
    assert body["supplement_rounds"] == 1
    assert body["supplement_status"] == "completed"
    assert body["coverage"]["critical_unresolved"] == []
    assert 4 in body["searched_pages"] and 52 in body["searched_pages"] and 65 in body["searched_pages"]

    family = client.get("/api/devices/d/family-view")
    assert family.status_code == 200
    assert family.json()["document_analysis"]["coverage"]["closed"] is True

    # Part-number confirmation remains an explicit human gate even when technical coverage is closed.
    before = client.get("/api/devices/d/final-review").json()
    assert before["overall_status"] == "blocked"
    models = client.get("/api/devices/d/models").json()
    assert models and all(m["verify_status"] == "pending" for m in models)
    for model in models:
        response = client.patch(f"/api/document-models/{model['id']}", json={
            "status": "confirmed", "value": model["ai_model"], "scope": model.get("scope") or "",
            "verified_by": "golden-acceptance",
        })
        assert response.status_code == 200

    # Closed coverage + confirmed part numbers means Final Review is deterministically unnecessary.
    final = client.post("/api/devices/d/final-review")
    assert final.status_code == 200
    assert final.json()["overall_status"] == "not_required"

    html = Path("storage_life/index.html").read_text(encoding="utf-8")
    assert "识别完整性（Coverage Gate）" in html
    assert "一键确认待确认料号" in html
    assert "Final Review：无需执行" in html
