import json
from pathlib import Path

from storage_life import ai, coverage


FIXTURE = Path(__file__).parent / "fixtures" / "gd25q64e_rev16_excerpt.json"


def _full_contract(found):
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


def test_gd25q64e_official_excerpt_closes_identity_and_lifetime_without_cross_device_fields(monkeypatch):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pages = [tuple(x) for x in fixture["pages"]]
    parts = ["GD25Q64EXEIG", "GD25Q64ENIG", "GD25Q64EQIG", "GD25Q64ETIG", "GD25Q64EBIG", "GD25Q64EFIG"]
    response = _full_contract({
        "manufacturer": {
            "value": "GigaDevice Semiconductor Inc.", "status": "found", "confidence": .99,
            "evidence": {"source_id": "pdf", "page": 66, "section": "Important Notice", "quote": "GigaDevice Semiconductor Inc."},
        },
        "product_family": {
            "value": "GD25Q64E", "status": "found", "confidence": .99,
            "evidence": {"source_id": "pdf", "page": 4, "section": "FEATURES", "quote": "64M-bit Serial Flash"},
        },
        "covered_part_numbers": {
            "value": "Valid Part Numbers", "status": "found", "confidence": .99,
            "scope_type": "part_number", "scope_values": parts,
            "evidence": {"source_id": "pdf", "page": 52, "section": "Valid Part Numbers", "quote": "GD25Q64EXEIG"},
        },
        "revision": {
            "value": "1.6", "status": "found", "confidence": .99,
            "evidence": {"source_id": "pdf", "page": 65, "section": "REVISION HISTORY", "quote": "1.6 Add VPWD and tPWD"},
        },
        "revision_date": {
            "value": "2024-04-28", "status": "found", "confidence": .99,
            "evidence": {"source_id": "pdf", "page": 65, "section": "REVISION HISTORY", "quote": "2024-4-28"},
        },
        "pe_cycles": {
            "value": "100000", "unit": "cycles", "status": "found", "confidence": .99,
            "evidence": {"source_id": "pdf", "page": 4, "section": "FEATURES", "quote": "Minimum 100,000 Program/Erase Cycles"},
        },
        "retention": {
            "value": "20", "unit": "years", "status": "found", "confidence": .99,
            "evidence": {"source_id": "pdf", "page": 4, "section": "FEATURES", "quote": "20-year data retention typical"},
        },
    })
    calls = []
    monkeypatch.setattr(ai, "_call", lambda instructions, payload, schema, client=None: (calls.append(payload) or response))

    result = ai.extract_specification_bundle_once(
        [{"source_id": "pdf", "pages": pages}], "NOR Flash", "GigaDevice", "GD25Q64E"
    )

    assert len(calls) == 1
    assert result["model_calls"] == 1
    assert result["supplement_rounds"] == 0
    assert result["coverage"]["critical_unresolved"] == []
    states = {x["field_key"]: x["state"] for x in result["coverage"]["states"]}
    assert states["pe_cycles"] == coverage.FOUND
    assert states["retention"] == coverage.FOUND
    assert states["tbw"] == coverage.NOT_APPLICABLE
    assert states["life_time_a"] == coverage.NOT_APPLICABLE
    assert result["document_identity"]["manufacturer"]["page"] == 66
    assert result["document_identity"]["revision"]["page"] == 65
    extracted_parts = {x["value"] for x in result["models"]}
    assert "GD25Q64E" not in extracted_parts
    assert set(parts) <= extracted_parts
    assert result["review_gate"]["status"] == "not_required"
