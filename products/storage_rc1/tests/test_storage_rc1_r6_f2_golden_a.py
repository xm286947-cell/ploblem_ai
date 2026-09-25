from __future__ import annotations

import time

from fastapi.testclient import TestClient

from storage_life import ai, core, document_pipeline
from storage_life.app import app


def _fake_markdown(_data, pages):
    text = "\n".join(str(p[1]) for p in pages)
    return "# Page 1\n" + text, [(1, text, "text")], {
        "parser_version": "f2-test",
        "markdown_sha256": "f2",
        "table_count": 0,
    }


def _fake_extract(*_args, **_kwargs):
    return [(1, "GigaDevice GD5F1GQ5 NAND Flash Endurance P/E Cycles 100000", "text")]


def _fake_agent_result(_pages, _device_type, _vendor, _model, source_id=""):
    evidence = {
        "source_id": source_id,
        "source_page": 1,
        "source_section": "Endurance",
        "source_text": "P/E Cycles 100000",
        "confidence": 0.99,
        "extraction_method": "agent_single_pass",
        "scope": "device",
    }
    coverage = {
        "states": [{"field_key": "pe_cycles", "state": "FOUND", "reason": "value_and_evidence_valid"}],
        "layers": {"identity": {}, "critical": {}, "diagnostic": {}, "general": {}},
        "critical_unresolved": [],
        "identity_unresolved": [],
        "diagnostic_unresolved": [],
    }
    return {
        "candidates": [{
            "canonical_name": "pe_cycles",
            "parameter_name": "P/E Cycles",
            "ai_value": "100000",
            "ai_unit": "cycles",
            "condition": "",
            "scope": "device",
            "source_page": 1,
            "source_section": "Endurance",
            "source_text": "P/E Cycles 100000",
            "confidence": 0.99,
            "extraction_method": "agent_single_pass",
            "evidence": [evidence],
        }],
        "analyzed_pages": [1],
        "schema_valid": True,
        "model_calls": 1,
        "unresolved_evidence": [],
        "review_required": True,
        "review_queue": ["pe_cycles"],
        "facts": [{
            "field_key": "pe_cycles",
            "status": "found",
            "value": "100000",
            "resolved_evidence": {"page": 1, "quote": "P/E Cycles 100000"},
        }],
        "expected_fields": ["pe_cycles"],
        "searched_pages": [1],
        "searched_sections": [{"page": 1, "section": "Endurance"}],
        "searched_fields": {"pe_cycles": [1]},
        "coverage": coverage,
        "coverage_layers": coverage["layers"],
        "document_analysis": {
            "searched_pages": [1],
            "searched_sections": [{"page": 1, "section": "Endurance"}],
            "coverage": coverage,
        },
        "document_identity": {},
        "models": [],
    }


def test_f2_golden_a_real_api_assembly(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "golden-a.sqlite3")
    monkeypatch.setattr(core, "extract_pdf", _fake_extract)
    monkeypatch.setattr(core, "extract_pdf_pages", lambda _data, _pages: _fake_extract())
    monkeypatch.setattr(document_pipeline, "build_markdown", _fake_markdown)
    monkeypatch.setattr(ai, "configured", lambda: True)
    monkeypatch.setattr(ai, "identify_device", lambda _pages: {
        "vendor": {"value": "GigaDevice", "page": 1, "quote": "GigaDevice", "confidence": 0.99},
        "model": {"value": "GD5F1GQ5", "page": 1, "quote": "GD5F1GQ5", "confidence": 0.99},
        "device_type": {"value": "NAND Flash", "page": 1, "quote": "NAND Flash", "confidence": 0.99},
    })
    monkeypatch.setattr(ai, "identify_document_identity", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(ai, "identify_models", lambda *_args, **_kwargs: {"models": [], "analyzed_pages": [1]})
    monkeypatch.setattr(ai, "extract_specification_once", _fake_agent_result)

    client = TestClient(app)
    pdf = ("golden-a.pdf", b"%PDF-1.4 F2 Golden A", "application/pdf")

    # PDF -> basic identity.
    identified = client.post("/api/documents/identify", files={"file": pdf})
    assert identified.status_code == 200
    identity = identified.json()
    assert identity["vendor"]["value"] == "GigaDevice"
    assert identity["model"]["value"] == "GD5F1GQ5"
    assert identity["device_type"]["value"] == "NAND Flash"

    # Human-confirmed identity -> parameter extraction job.
    created = client.post(
        "/api/documents/jobs",
        files={"file": ("golden-a.pdf", b"%PDF-1.4 F2 Golden A", "application/pdf")},
        data={
            "vendor": "GigaDevice",
            "model": "GD5F1GQ5",
            "device_type": "NAND Flash",
            "models_json": "[]",
        },
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]

    job = None
    for _ in range(200):
        job = client.get(f"/api/documents/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert job is not None and job["status"] == "completed", job
    assert job["result"]["candidate_count"] == 1
    assert job["result"]["coverage"]["states"][0]["state"] == "FOUND"
    device_id = job["result"]["device_id"]

    # Coverage -> Review workbench -> Evidence.
    workbench = client.get(f"/api/product/devices/{device_id}/review-workbench")
    assert workbench.status_code == 200
    row = next(x for x in workbench.json()["rows"] if x["canonical_name"] == "pe_cycles")
    assert row["coverage_status"] == "FOUND"
    assert row["candidate_id"]
    assert row["evidence"]
    assert row["evidence"][0]["source_text"] == "P/E Cycles 100000"

    # Human Review -> formal Device Fact keeps Evidence.
    reviewed = client.patch(
        f"/api/candidates/{row['candidate_id']}",
        json={"status": "confirmed", "value": "100000", "unit": "cycles", "verified_by": "f2-gate"},
    )
    assert reviewed.status_code == 200
    facts = client.get(f"/api/product/devices/{device_id}/facts")
    assert facts.status_code == 200
    fact = next(x for x in facts.json()["facts"] if x["canonical_name"] == "pe_cycles")
    assert fact["value"] == "100000"
    assert fact["evidence"]
    assert fact["evidence"][0]["source_text"] == "P/E Cycles 100000"


def test_f2_ui_exposes_live_golden_a_stage_and_failure_recovery():
    from pathlib import Path

    html = (Path(__file__).parents[1] / "storage_life" / "index.html").read_text(encoding="utf-8")
    for token in (
        'id="goldenAAssembly"',
        'id="goldenAStatus"',
        "GOLDEN_A_ORDER",
        "setGoldenAStage('IDENTITY','PASS'",
        "setGoldenAStage('EXTRACTION','RUNNING'",
        "setGoldenAStage('COVERAGE','PASS'",
        "syncGoldenAReview()",
        "有证据参数",
    ):
        assert token in html
    # Background job failure must not leave P08 permanently locked.
    assert "IMPORT_JOB_CREATING=false" in html
    assert "button.disabled=false" in html
    assert "button.textContent='进入参数识别与覆盖度分析'" in html
