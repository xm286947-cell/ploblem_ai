from __future__ import annotations

from storage_life import ai
from storage_life.runtime_domain_strategy import (
    EMMC_ANALYSIS_FIELDS,
    EMMC_FIELD_ORDER,
    EMMC_IDENTITY_FIELDS,
)


def test_runtime_emmc_single_pass_uses_frozen_37_field_contract(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    schema, fields = ai._single_pass_schema("eMMC")
    assert fields == list(EMMC_FIELD_ORDER)
    assert len(fields) == 37
    item = schema["properties"]["fields"]["items"]
    assert item["properties"]["field_key"]["enum"] == list(EMMC_FIELD_ORDER)
    assert "knowledge_type" in item["required"]
    assert len(EMMC_IDENTITY_FIELDS) == 8
    assert len(EMMC_ANALYSIS_FIELDS) == 29


def test_runtime_emmc_candidate_view_uses_non_identity_contract_fields(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    fields = ai.expected_fields("eMMC")
    assert [x["canonical_name"] for x in fields] == list(EMMC_ANALYSIS_FIELDS)
    assert len(fields) == 29


def test_runtime_emmc_37_field_adapter_resolves_synthetic_evidence(monkeypatch):
    import json
    from pypdf import PdfReader
    from storage_life import core
    from test_support.mock_router import _stage_payload

    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    root = core.ROOT
    pdf = root / "examples" / "synthetic_emmc.pdf"
    pages = core.extract_pdf(pdf.read_bytes())
    schema, fields = ai._single_pass_schema("eMMC")
    payload = {
        "device_type": "eMMC",
        "primary_source_id": "fixture",
        "pages": [{"source_id": "fixture", "page": p, "text": text, "method": method} for p, text, method in pages],
    }
    body = {
        "messages": [
            {"role": "system", "content": "You extract storage-device datasheet facts in ONE pass. JSON Schema: " + json.dumps(schema)},
            {"role": "user", "content": json.dumps(payload)},
        ]
    }
    provider_result = _stage_payload(body)
    adapted = ai._adapt_single_pass(
        provider_result,
        pages,
        "eMMC",
        "Demo Storage",
        "SYN-EMMC-1",
        "fixture",
        source_pages={"fixture": pages},
        covered_fields=set(fields),
    )
    assert adapted["schema_valid"] is True
    assert adapted["expected_fields"] == list(EMMC_FIELD_ORDER)
    assert len(adapted["facts"]) == 37
    assert len({x["field_key"] for x in adapted["facts"]}) == 37
    found = [x for x in adapted["facts"] if x["status"] == "found"]
    assert found
    assert all(x["knowledge_type"] in {"specification", "diagnostic_capability", "device_requirement"} for x in adapted["facts"])
    assert any((x.get("resolved_evidence") or {}).get("page") == 1 for x in found)
    assert adapted["unresolved_evidence"] == []


def test_mock_fault_target_is_dedicated_emmc_extraction_stage():
    from test_support.mock_router import _stage_name

    identify = {
        "messages": [
            {"role": "system", "content": "Please identify basic device metadata from this PDF."},
            {"role": "user", "content": "{}"},
        ]
    }
    extract = {
        "messages": [
            {"role": "system", "content": "You extract storage-device datasheet facts in ONE pass."},
            {"role": "user", "content": "{}"},
        ]
    }
    assert _stage_name(identify) == "identify_basic"
    assert _stage_name(extract) == "emmc_parameter_extract"


def test_mock_router_understands_runtime_owned_provider_envelope(monkeypatch):
    import json
    from test_support.mock_router import _stage_name, _stage_payload

    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    schema, _fields = ai._single_pass_schema("eMMC")
    provider_payload = {
        "device_type": "eMMC",
        "primary_source_id": "fixture",
        "pages": [
            {
                "source_id": "fixture",
                "page": 1,
                "text": "Vendor: Demo Storage\nModel: SYN-EMMC-1\nCapacity: 64 GB",
                "method": "text",
            }
        ],
    }
    body = {
        "messages": [
            {
                "role": "system",
                "content": "You extract storage-device datasheet facts in ONE pass for the Storage product.",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instructions": "You extract storage-device datasheet facts in ONE pass.",
                        "provider_payload": provider_payload,
                        "schema": schema,
                    }
                ),
            },
        ]
    }

    assert _stage_name(body) == "emmc_parameter_extract"
    result = _stage_payload(body)
    assert len(result["fields"]) == 37
    assert result["fields"][0]["field_key"] == EMMC_FIELD_ORDER[0]


def test_runtime_emmc_reviewed_spec_accepts_37_field_contract_candidates(monkeypatch, tmp_path):
    from storage_life import core
    from uuid import uuid4
    import json

    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "storage_life.sqlite3")
    source_id = uuid4().hex
    device_id = uuid4().hex
    with core.connect() as con:
        con.execute(
            "INSERT INTO sources(id,filename,sha256,local_path,original_url,publisher,page_count,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (source_id, "fixture.pdf", "a"*64, str(tmp_path/"fixture.pdf"), "", "test", 1, core.now()),
        )
        con.execute(
            "INSERT INTO devices(id,source_id,vendor,model,device_type) VALUES (?,?,?,?,?)",
            (device_id, source_id, "Demo", "SYN-EMMC-1", "eMMC"),
        )
        facts = [
            ("pe_cycle", "3000", "cycles"),
            ("device_life_time_est_typ_a", "supported", ""),
            ("device_life_time_est_typ_b", "supported", ""),
            ("pre_eol_info", "supported", ""),
            ("bkops_status", "supported", ""),
        ]
        for key, value, unit in facts:
            cid = uuid4().hex
            con.execute(
                """INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,condition,scope,source_page,source_section,source_text,confidence,extraction_method)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, device_id, key, key, value, unit, "", "product family", 1, "fixture", f"{key} {value}", 0.99, "agent_single_pass"),
            )
            eid = uuid4().hex
            con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)", (eid, cid, 1, "fixture", f"{key} {value}", 0.99, "agent_single_pass", "product family"))
            con.execute("INSERT INTO candidate_evidence_provenance(evidence_id,source_id) VALUES (?,?)", (eid, source_id))

    specs = core.rebuild_reviewed_specifications(device_id)
    keys = {x["canonical_name"] for x in specs}
    assert {"pe_cycle", "device_life_time_est_typ_a", "device_life_time_est_typ_b", "pre_eol_info"} <= keys
    status = core.specification_workflow_status(device_id)
    assert status["status"] == "pending_confirmation"
    assert status["reviewed_spec_count"] >= 4
    assert status["missing_critical_fields"] == []
    view = core.reviewed_specs(device_id)
    assert view["visible_field_count"] >= 4
    assert any("标准健康诊断" in group["name"] for group in view["groups"])
    conclusion = core.get_device_conclusion(device_id)
    assert conclusion["conclusion_status"] in {"partial", "complete"}
    assert "寿命估算A" in conclusion["diagnostic_summary"]
