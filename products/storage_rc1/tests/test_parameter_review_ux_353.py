from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from storage_life import core
from storage_life.app import app


def _candidate(con, *, cid, field, value, with_evidence=True):
    con.execute(
        """INSERT INTO candidates(
        id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
        condition,scope,source_page,source_section,source_text,confidence,extraction_method
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            cid,
            "d",
            field,
            field,
            value,
            "",
            "",
            "device",
            1,
            "Health",
            f"{field} = {value}",
            0.99,
            "agent_text",
        ),
    )
    if with_evidence:
        con.execute(
            "INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
            (f"e-{cid}", cid, 1, "Health", f"{field} = {value}", 0.99, "agent_text", "device"),
        )
        con.execute(
            "INSERT INTO candidate_evidence_provenance VALUES (?,?)",
            (f"e-{cid}", "s"),
        )


def _seed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "review-ux-353.sqlite3")
    now = core.now()
    pdf = tmp_path / "spec.pdf"
    pdf.write_bytes(b"%PDF sample")
    states = [
        {"field_key": "life_time_a", "state": "FOUND", "reason": "value_and_evidence_valid", "layer": "diagnostic"},
        {"field_key": "life_time_b", "state": "FOUND", "reason": "value_and_evidence_valid", "layer": "diagnostic"},
        {"field_key": "pre_eol", "state": "NOT_SPECIFIED", "reason": "relevant_section_searched_without_supported_value", "layer": "diagnostic"},
        {"field_key": "bkops", "state": "NOT_APPLICABLE", "reason": "outside_device_profile", "layer": "diagnostic"},
        {"field_key": "ext_csd_health_report", "state": "UNRESOLVED", "reason": "ambiguous_candidates", "layer": "diagnostic"},
    ]
    with core.connect() as con:
        con.execute(
            "INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
            ("s", pdf.name, "sha", str(pdf), "", "Vendor", 2, now),
        )
        con.execute(
            "INSERT INTO devices VALUES (?,?,?,?,?)",
            ("d", "Vendor", "EMMC-X", "eMMC", "s"),
        )
        _candidate(con, cid="c-trusted", field="life_time_a", value="0x03", with_evidence=True)
        _candidate(con, cid="c-no-evidence", field="life_time_b", value="0x04", with_evidence=False)
        _candidate(con, cid="c-ambiguous", field="ext_csd_health_report", value="health", with_evidence=True)
        con.execute(
            """INSERT INTO extraction_runs(
            id,device_id,source_id,extraction_mode,schema_valid,model_calls,
            unresolved_evidence_json,review_required,review_queue_json,facts_json,
            expected_fields_json,searched_pages_json,searched_sections_json,searched_fields_json,
            coverage_json,coverage_layers_json,document_analysis_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "r",
                "d",
                "s",
                "test",
                1,
                1,
                "[]",
                0,
                "[]",
                "[]",
                "[]",
                "[1]",
                "[]",
                "{}",
                json.dumps({"states": states}),
                "{}",
                "{}",
                now,
            ),
        )


def _by_name(workbench):
    result = {}
    for row in workbench["rows"]:
        result.setdefault(row["canonical_name"], []).append(row)
    return result


def test_review_workbench_is_exception_first_and_non_contradictory(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    body = TestClient(app).get("/api/product/devices/d/review-workbench").json()
    by = _by_name(body)

    assert by["life_time_a"][0]["ux_state"] == "TRUSTED"
    assert by["life_time_a"][0]["batch_confirmable"] is True

    assert by["life_time_b"][0]["ux_state"] == "NEEDS_ATTENTION"
    assert "no_evidence" in by["life_time_b"][0]["exception_reasons"]
    assert by["life_time_b"][0]["batch_confirmable"] is False

    assert by["pre_eol"][0]["ux_state"] == "UNKNOWN"
    assert by["pre_eol"][0]["ux_label"] == "无法从规格书确定"

    assert by["bkops"][0]["ux_state"] == "NOT_APPLICABLE"
    assert by["bkops"][0]["ux_label"] == "不适用"

    assert by["ext_csd_health_report"][0]["ux_state"] == "NEEDS_ATTENTION"
    assert "ambiguity_or_conflict" in by["ext_csd_health_report"][0]["exception_reasons"]

    summary = body["ux_summary"]
    assert summary["trusted"] == 1
    assert summary["needs_attention"] >= 2
    assert summary["unknown"] >= 1
    assert summary["not_applicable"] >= 1


def test_batch_confirm_only_confirms_eligible_and_preserves_field_audit(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    client = TestClient(app)

    result = client.post(
        "/api/product/devices/d/review/batch-confirm",
        json={"verified_by": "reviewer-a"},
    )
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["requested_count"] == 1
    assert payload["confirmed_count"] == 1
    assert payload["failed_count"] == 0

    refreshed = client.get("/api/product/devices/d/review-workbench").json()
    by = _by_name(refreshed)
    assert by["life_time_a"][0]["ux_state"] == "CONFIRMED"
    assert by["life_time_b"][0]["ux_state"] == "NEEDS_ATTENTION"

    history = client.get("/api/candidates/c-trusted/review-history").json()
    assert history[0]["confirm_mode"] == "batch"
    assert history[0]["ai_value"] == "0x03"
    assert history[0]["new_final_value"] == "0x03"
    assert history[0]["prior_status"] == "pending"
    assert history[0]["new_status"] == "confirmed"
    assert history[0]["evidence_refs"][0]["evidence_id"] == "e-c-trusted"
    assert history[0]["evidence_refs"][0]["source_id"] == "s"


def test_complete_review_does_not_weaken_confirmed_device_fact_gate(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    client = TestClient(app)
    client.post(
        "/api/product/devices/d/review/batch-confirm",
        json={"verified_by": "reviewer-a"},
    )

    response = client.post("/api/product/devices/d/review/complete")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "REVIEW_NOT_COMPLETE"
    assert detail["workflow"]["formal_ready"] is False
    assert detail["blockers"]


def test_ui_exposes_batch_confirm_exception_queue_and_advanced_raw_states():
    html = Path("storage_life/index.html").read_text(encoding="utf-8")
    assert "确认全部可信项" in html
    assert "处理异常项" in html
    assert "完成确认" in html
    assert "无法从规格书确定" in html
    assert "N/A 不适用" in html
    assert "batchConfirmTrusted" in html
    assert "completeParameterReview" in html
    assert "Coverage / Review 技术状态" in html
