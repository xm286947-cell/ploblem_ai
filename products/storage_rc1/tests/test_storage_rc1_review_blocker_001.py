from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from storage_life import core
from storage_life.app import app


def _insert_candidate(con, *, cid, did, field, value, unit="", page=1):
    con.execute(
        """INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
        condition,scope,source_page,source_section,source_text,confidence,extraction_method)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, did, field, field, value, unit, "AI condition", "device", page, "Health", f"{field} = {value} {unit}".strip(), .98, "agent_text"),
    )
    con.execute(
        "INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
        (f"e-{cid}", cid, page, "Health", f"{field} = {value} {unit}".strip(), .98, "agent_text", "device"),
    )
    con.execute("INSERT INTO candidate_evidence_provenance VALUES (?,?)", (f"e-{cid}", f"s-{did}"))


def _seed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "storage_life.sqlite3")
    now = core.now()
    with core.connect() as con:
        for did, vendor, model in (("d1", "VendorA", "EMMC-A"), ("d2", "VendorB", "EMMC-B")):
            sid = f"s-{did}"
            pdf = tmp_path / f"{did}.pdf"
            pdf.write_bytes(b"%PDF sample")
            con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", (sid, pdf.name, f"sha-{did}", str(pdf), "", vendor, 4, now))
            con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", (did, vendor, model, "eMMC", sid))
            con.execute(
                """INSERT INTO document_identities(id,source_id,device_id,identity_key,document_number,revision,revision_date,document_status,document_variant,language,identity_source,identity_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"i-{did}", sid, did, f"key-{did}", f"DOC-{did}", "A", "2026-09-24", "released", "", "en", "test", "{}", now),
            )
        _insert_candidate(con, cid="c1", did="d1", field="life_time_a", value="0x03", page=1)
        _insert_candidate(con, cid="c2", did="d1", field="life_time_b", value="0x04", page=2)
        _insert_candidate(con, cid="c3", did="d1", field="pre_eol", value="0x01", page=3)
        _insert_candidate(con, cid="c4", did="d2", field="life_time_a", value="0x09", page=1)
        for rid, did in (("r1", "d1"), ("r2", "d2")):
            states = [
                {"field_key": "life_time_a", "state": "FOUND", "reason": "value_and_evidence_valid"},
                {"field_key": "life_time_b", "state": "FOUND", "reason": "value_and_evidence_valid"},
                {"field_key": "pre_eol", "state": "FOUND", "reason": "value_and_evidence_valid"},
            ]
            con.execute(
                """INSERT INTO extraction_runs(id,device_id,source_id,extraction_mode,schema_valid,model_calls,
                unresolved_evidence_json,review_required,review_queue_json,facts_json,expected_fields_json,searched_pages_json,
                searched_sections_json,searched_fields_json,coverage_json,coverage_layers_json,document_analysis_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, did, f"s-{did}", "test", 1, 1, "[]", 0, "[]", "[]", "[]", "[1,2,3]", "[]", "{}", json.dumps({"states": states}), "{}", "{}", now),
            )
    core.rebuild_reviewed_specifications("d1")
    core.rebuild_reviewed_specifications("d2")


def test_p08_review_workbench_and_device_fact_gate(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    client = TestClient(app)

    detail = client.get("/api/product/devices/d1").json()
    by = {x["canonical_name"]: x for x in detail["slots"]}
    assert by["life_time_a"]["coverage_status"] == "FOUND"
    assert by["life_time_a"]["review_status"] == "UNREVIEWED"
    assert by["life_time_a"]["ai_value"] == "0x03"
    assert by["life_time_a"]["value"] is None  # AI value is never a formal Device Fact.

    workbench = client.get("/api/product/devices/d1/review-workbench").json()
    row = next(x for x in workbench["rows"] if x["candidate_id"] == "c1")
    assert row["coverage_status"] == "FOUND"
    assert row["review_status"] == "UNREVIEWED"
    assert row["evidence"][0]["source_filename"] == "d1.pdf"
    assert row["evidence"][0]["revision"] == "A"

    # Confirm unchanged AI value.
    r = client.patch("/api/candidates/c1", json={
        "status": "confirmed", "value": "0x03", "unit": "", "condition": "AI condition", "scope": "device", "verified_by": "reviewer-a"
    })
    assert r.status_code == 200
    assert r.json()["review_action"] == "confirm"

    # Edit + Confirm keeps immutable AI value and creates a review-history version.
    r = client.patch("/api/candidates/c2", json={
        "status": "confirmed", "value": "0x05", "unit": "", "condition": "human checked", "scope": "device", "verified_by": "reviewer-b"
    })
    assert r.status_code == 200
    assert r.json()["review_action"] == "edit_confirm"

    # Reject is persisted and must not create Device Fact.
    r = client.patch("/api/candidates/c3", json={
        "status": "rejected", "value": "0x01", "unit": "", "condition": "AI condition", "scope": "device", "verified_by": "reviewer-c"
    })
    assert r.status_code == 200
    assert r.json()["review_action"] == "reject"

    # Re-query simulates refresh persistence.
    refreshed = client.get("/api/product/devices/d1/review-workbench").json()
    states = {x["candidate_id"]: x for x in refreshed["rows"] if x["candidate_id"]}
    assert states["c1"]["review_status"] == "CONFIRMED"
    assert states["c2"]["review_status"] == "CONFIRMED"
    assert states["c2"]["ai_value"] == "0x04"
    assert states["c2"]["human_value"] == "0x05"
    assert states["c3"]["review_status"] == "REJECTED"
    assert states["c2"]["history"][0]["action"] == "edit_confirm"

    facts = client.get("/api/product/devices/d1/facts").json()
    assert facts["gate"] == "CONFIRMED_ONLY"
    by_fact = {x["canonical_name"]: x for x in facts["facts"]}
    assert by_fact["life_time_a"]["value"] == "0x03"
    assert by_fact["life_time_b"]["value"] == "0x05"
    assert "pre_eol" not in by_fact


def test_downstream_consumers_do_not_receive_unreviewed_ai_values(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    client = TestClient(app)
    client.patch("/api/candidates/c1", json={
        "status": "confirmed", "value": "0x03", "unit": "", "condition": "AI condition", "scope": "device", "verified_by": "reviewer-a"
    })

    comparison = client.post("/api/product/compare", json={"device_ids": ["d1", "d2"]}).json()
    life = next(x for x in comparison["rows"] if x["canonical_name"] == "life_time_a")
    assert life["cells"]["d1"]["value"] == "0x03"
    assert life["cells"]["d2"]["review_status"] == "UNREVIEWED"
    assert life["cells"]["d2"]["value"] is None
    assert life["cells"]["d2"]["evidence"] == []

    diag = client.get("/api/product/diagnostics?device_id=d2").json()
    life_diag = next(x for x in diag["items"] if x["canonical_name"] == "life_time_a")
    assert life_diag["review_status"] == "UNREVIEWED"
    assert life_diag["evidence"] == []

    impact = client.get("/api/product/change-impact?old_id=d1&new_id=d2").json()
    life_impact = next(x for x in impact["items"] if x["canonical_name"] == "life_time_a")
    assert life_impact["new"]["value"] is None
    assert life_impact["confidence"] == "LOW"
    assert any("未确认/缺失事实" in x for x in impact["unknowns"])


def test_product_html_exposes_unified_review_workbench(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    html = TestClient(app).get("/").text
    assert "参数确认工作台" in html
    assert "Edit + Confirm" in html
    assert "Coverage Status" in html
    assert "Review Status" in html
    assert "openReview" in html
    assert "Confirmed Device Fact" in html
