"""Part-scoped NAND consumer regressions; no Provider or formal release calls."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storage_life import ai, core, parameter_baseline, product_api
from storage_life.app import app
from storage_life.nand_engineering_decision import build_nand_engineering_decision


PART_A = "TC58NVG0S3HBAI4"
PART_B = "TC58NVG2S0HBAI4"
FAMILY_LIST = f"{PART_A} | {PART_B}"


def _evidence(part: str, page_size: str, page: int) -> list[dict]:
    return [{
        "source_id": "source-kioxia",
        "evidence_id": f"ev-{part}",
        "source_page": page,
        "source_section": "Part Number / Product Brief table",
        "source_text": f"{part} · ( {page_size}+128 )x8",
        "confidence": 0.95,
        "scope": part,
        "extraction_method": "source_table_part_number",
    }]


def _fixture(monkeypatch):
    models = [
        {"id": "model-a", "device_id": "nand", "ai_model": PART_A,
         "final_model": FAMILY_LIST, "scope": PART_A, "verify_status": "pending",
         "source_page": 2, "source_text": f"{PART_A} 1G (2048+128)x8"},
        {"id": "model-b", "device_id": "nand", "ai_model": PART_B,
         "final_model": FAMILY_LIST, "scope": PART_B, "verify_status": "pending",
         "source_page": 2, "source_text": f"{PART_B} 4G (4096+256)x8"},
    ]
    candidates = [
        {"id": "candidate-a", "device_id": "nand", "canonical_name": "page_size",
         "parameter_name": "Page Size", "ai_value": "2048", "ai_unit": "bytes",
         "final_value": None, "final_unit": None, "condition": "spare=128 bytes",
         "scope": PART_A, "source_page": 2, "verify_status": "pending",
         "extraction_method": "source_table_part_number", "evidence": _evidence(PART_A, "2048", 2)},
        {"id": "candidate-b", "device_id": "nand", "canonical_name": "page_size",
         "parameter_name": "Page Size", "ai_value": "4096", "ai_unit": "bytes",
         "final_value": None, "final_unit": None, "condition": "spare=256 bytes",
         "scope": PART_B, "source_page": 2, "verify_status": "pending",
         "extraction_method": "source_table_part_number", "evidence": _evidence(PART_B, "4096", 2)},
    ]
    devices = [{"id": "nand", "vendor": "KIOXIA Corporation", "model": "SLC NAND",
                "device_type": "NAND Flash", "filename": "kioxia-product-brief.pdf"}]
    monkeypatch.setattr(core, "list_devices", lambda: devices)
    monkeypatch.setattr(core, "list_models", lambda _device_id: models)
    monkeypatch.setattr(core, "list_candidates", lambda _device_id: candidates)
    monkeypatch.setattr(parameter_baseline, "product_fields", lambda *_args: [
        {"canonical_name": "page_size", "parameter_name": "Page Size", "aliases": ["page_size"]}
    ])
    return models, candidates


def test_api_keeps_pending_part_identity_and_values_out_of_formal_facts(monkeypatch):
    _models, _candidates = _fixture(monkeypatch)
    client = TestClient(app)

    pending = client.get("/api/product/devices/nand/parts/model-a")
    assert pending.status_code == 200
    body = pending.json()
    assert body["selected_part"]["part_number"] == PART_A
    assert body["selected_part"]["part_number"] != FAMILY_LIST
    assert body["can_consume"] is False
    page = body["facts"][0]
    assert page["status"] == "PART_NUMBER_UNREVIEWED"
    assert page["value"] is None
    assert page["candidate_value"] == "2048"
    assert page["evidence"][0]["source_page"] == 2
    assert PART_A in page["evidence"][0]["source_text"]
    assert body["confirmed_facts"] == []


def test_compare_requires_human_confirmation_and_never_cross_binds_variants(monkeypatch):
    models, candidates = _fixture(monkeypatch)
    client = TestClient(app)
    payload = {"selections": [
        {"device_id": "nand", "model_id": "model-a"},
        {"device_id": "nand", "model_id": "model-b"},
    ]}

    pending = client.post("/api/product/compare/parts", json=payload)
    assert pending.status_code == 200
    result = pending.json()
    assert result["status"] == "PART_NUMBER_REVIEW_REQUIRED"
    assert {item["part_number"] for item in result["selections"]} == {PART_A, PART_B}
    row = next(item for item in result["rows"] if item["canonical_name"] == "page_size")
    left = row["cells"]["nand:model-a"]
    right = row["cells"]["nand:model-b"]
    assert (left["value"], left["candidate_value"], left["status"]) == (None, "2048", "PART_NUMBER_UNREVIEWED")
    assert (right["value"], right["candidate_value"], right["status"]) == (None, "4096", "PART_NUMBER_UNREVIEWED")
    assert row["is_difference"] is False  # Unreviewed values are not formal differences.
    assert PART_A in left["evidence"][0]["source_text"]
    assert PART_B in right["evidence"][0]["source_text"]

    # This is isolated test state representing a reviewer-confirmed precondition;
    # it is not an actual human review of the real source.
    for model in models:
        model["verify_status"] = "confirmed"
        model["final_model"] = model["ai_model"]
    for candidate in candidates:
        candidate["verify_status"] = "confirmed"
        candidate["final_value"] = candidate["ai_value"]
        candidate["final_unit"] = candidate["ai_unit"]

    confirmed = client.post("/api/product/compare/parts", json=payload)
    assert confirmed.status_code == 200
    result = confirmed.json()
    assert result["status"] == "PART_COMPARISON_READY"
    row = next(item for item in result["rows"] if item["canonical_name"] == "page_size")
    assert row["is_difference"] is True
    assert row["cells"]["nand:model-a"]["value"] == "2048"
    assert row["cells"]["nand:model-b"]["value"] == "4096"


def test_selected_part_does_not_inherit_another_parts_only_candidate(monkeypatch):
    models, candidates = _fixture(monkeypatch)
    models[0]["verify_status"] = "confirmed"
    models[0]["final_model"] = PART_A
    candidates[:] = [candidates[1]]
    result = product_api.part_number_facts("nand", "model-a")
    page = next(item for item in result["facts"] if item["canonical_name"] == "page_size")
    assert page["status"] == "NOT_SPECIFIED_FOR_SELECTED_PART"
    assert page["value"] is None
    assert page["candidate_value"] is None
    assert page["evidence"] == []


def test_confirmed_composite_model_identity_is_rejected(monkeypatch):
    models, _candidates = _fixture(monkeypatch)
    models[0]["verify_status"] = "confirmed"
    models[0]["final_model"] = FAMILY_LIST
    try:
        product_api.part_number_facts("nand", "model-a")
    except ValueError as exc:
        assert str(exc) == "PART_NUMBER_IDENTITY_AMBIGUOUS"
    else:
        raise AssertionError("composite part identity must not be consumed as one orderable part")


def test_family_matrix_uses_source_bound_single_part_while_model_is_pending(monkeypatch):
    class Query:
        def fetchone(self):
            return {"id": "nand", "vendor": "KIOXIA Corporation", "model": "SLC NAND",
                    "device_type": "NAND Flash", "source_id": "source-kioxia"}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return Query()

    models, candidates = _fixture(monkeypatch)
    monkeypatch.setattr(core, "connect", Connection)
    monkeypatch.setattr(core, "list_models", lambda _id: models)
    monkeypatch.setattr(core, "list_candidates", lambda _id: candidates)
    monkeypatch.setattr(core, "get_document_identity", lambda _id: None)
    monkeypatch.setattr(core, "get_extraction_run", lambda _id: None)
    monkeypatch.setattr(core, "get_device_conclusion", lambda _id: None)
    monkeypatch.setattr(core, "specification_workflow_status", lambda _id: {"formal_ready": False})

    view = core.family_view("nand")
    assert {item["model"] for item in view["models"]} == {PART_A, PART_B}
    assert all(item["verify_status"] == "pending" for item in view["models"])
    assert {item["scope"] for item in view["variant_specs"]} == {PART_A, PART_B}


def test_engineering_consumption_requires_an_explicit_part_when_document_lists_variants(monkeypatch):
    monkeypatch.setattr(product_api, "device_slots", lambda _id: {
        "device": {"id": "nand", "vendor": "KIOXIA", "model": "SLC NAND", "device_type": "NAND Flash"},
        "device_facts": [],
        "orderable_part_candidates": [
            {"id": "model-a", "part_number": PART_A, "verify_status": "pending"}
        ],
    })
    try:
        build_nand_engineering_decision("nand", {})
    except ValueError as exc:
        assert str(exc) == "PART_NUMBER_SELECTION_REQUIRED"
    else:
        raise AssertionError("family-level inputs must not bypass an available orderable-part review")


def test_static_ecc_capability_routes_only_to_specification_review():
    fields = parameter_baseline.product_fields("NAND Flash", ai.expected_fields("NAND Flash"))
    requirement = next(item for item in fields if item["canonical_name"] == "ecc_requirement")
    observability = next(item for item in fields if item["canonical_name"] == "ecc_observability")
    assert "ecc_capability" in requirement["aliases"]
    assert "ecc_capability" not in observability["aliases"]


@pytest.mark.skipif(
    not os.environ.get("HC620_KIOXIA_PDF") or not os.environ.get("HC620_ISOLATED_DATA_DIR"),
    reason="opt-in real-PDF source replay; requires an explicit source PDF and empty isolated data directory",
)
def test_real_kioxia_pdf_source_replay_reaches_pending_candidate_review(monkeypatch):
    """Use the actual PDF parser/table adapter; deterministic offline response, never Provider."""
    source_pdf = Path(os.environ["HC620_KIOXIA_PDF"]).expanduser().resolve()
    data_dir = Path(os.environ["HC620_ISOLATED_DATA_DIR"]).expanduser().resolve()
    assert source_pdf.is_file()
    assert data_dir.is_dir() and not any(data_dir.iterdir()), "isolated data directory must be empty"
    monkeypatch.setattr(core, "DATA", data_dir)
    monkeypatch.setattr(core, "DB", data_dir / "storage_life.sqlite3")
    monkeypatch.setattr(ai, "configured", lambda: True)
    _schema, expected = ai._single_pass_schema("NAND Flash")
    fields = [{
        "field_key": key, "value": None, "unit": None, "condition": None,
        "scope_type": "product_family", "scope_values": [], "status": "missing",
        "confidence": 0, "derived": False, "knowledge_type": "specification",
        "evidence": None, "conflict_evidence": [],
    } for key in expected]
    monkeypatch.setattr(ai, "_primary_or_secondary_extraction",
                        lambda *_args, **_kwargs: ({"fields": fields}, None, 0))
    monkeypatch.setattr(ai, "_run_critical_targeted_supplement",
                        lambda result, *_args, **_kwargs: result)

    imported = core.import_document(
        source_pdf.name, source_pdf.read_bytes(), "KIOXIA Corporation", "SLC NAND",
        "NAND Flash",
    )
    models = core.list_models(imported["device_id"])
    model_ids_by_pn = {item["ai_model"]: item["id"] for item in models}
    assert set(model_ids_by_pn) >= {PART_A, PART_B}
    assert all(item["verify_status"] == "pending" for item in models)
    candidates = core.list_candidates(imported["device_id"])
    page_rows = [item for item in candidates if item["canonical_name"] == "page_size"]
    assert {item["scope"] for item in page_rows} >= {PART_A, PART_B}
    assert {item["ai_value"] for item in page_rows} >= {"2048", "4096"}
    assert all(item["verify_status"] == "pending" for item in page_rows)
    assert all(item["evidence"][0]["source_page"] == 2 for item in page_rows)
    assert all(item["scope"] in item["evidence"][0]["source_text"] for item in page_rows)

    client = TestClient(app)
    matrix = client.get(f"/api/devices/{imported['device_id']}/family-view")
    assert matrix.status_code == 200
    assert {item["model"] for item in matrix.json()["models"]} >= {PART_A, PART_B}
    compare = client.post("/api/product/compare/parts", json={"selections": [
        {"device_id": imported["device_id"], "model_id": model_ids_by_pn[PART_A]},
        {"device_id": imported["device_id"], "model_id": model_ids_by_pn[PART_B]},
    ]})
    assert compare.status_code == 200
    compared = compare.json()
    assert compared["status"] == "PART_NUMBER_REVIEW_REQUIRED"
    page_row = next(item for item in compared["rows"] if item["canonical_name"] == "page_size")
    capacity_row = next(item for item in compared["rows"] if item["canonical_name"] == "capacity")
    by_part = {item["part_number"]: item for item in compared["selections"]}
    for part, page_bytes, capacity_gbit in ((PART_A, "2048", "1"), (PART_B, "4096", "4")):
        selection_id = by_part[part]["selection_id"]
        page_cell = page_row["cells"][selection_id]
        capacity_cell = capacity_row["cells"][selection_id]
        assert page_cell["status"] == "PART_NUMBER_UNREVIEWED"
        assert page_cell["candidate_value"] == page_bytes
        assert page_cell["scope"] == part
        assert page_cell["evidence"]
        assert part in page_cell["evidence"][0]["source_text"]
        assert capacity_cell["candidate_value"] == capacity_gbit
        assert capacity_cell["scope"] == part
    assert imported["model_calls"] == 0


@pytest.mark.skipif(
    not os.environ.get("HC620_GD5_PDF") or not os.environ.get("HC620_ISOLATED_DATA_DIR"),
    reason="opt-in real-PDF source replay; requires an explicit source PDF and empty isolated data directory",
)
def test_real_gd5_pdf_replay_persists_pe_ecc_and_nvb_with_original_pages(monkeypatch):
    """Replay already-known source facts into the isolated Candidate/Evidence path, offline."""
    source_pdf = Path(os.environ["HC620_GD5_PDF"]).expanduser().resolve()
    data_dir = Path(os.environ["HC620_ISOLATED_DATA_DIR"]).expanduser().resolve()
    assert source_pdf.is_file()
    assert data_dir.is_dir() and not any(data_dir.iterdir()), "isolated data directory must be empty"
    monkeypatch.setattr(core, "DATA", data_dir)
    monkeypatch.setattr(core, "DB", data_dir / "storage_life.sqlite3")
    monkeypatch.setattr(ai, "configured", lambda: True)
    _schema, expected = ai._single_pass_schema("NAND Flash")
    facts = {
        "pe_cycles": ("100K", "cycles", "With ECC", 4, "P/E cycles with ECC: 100K"),
        "ecc_capability": ("4", "bits/528 bytes", "Internal ECC", 4, "4bits /528byte"),
        "pages_per_block": ("64", "pages", "Array Organization", 10, "1 block  = (2K + 128)bytes x 64 pages"),
        "minimum_valid_blocks": (
            "1004", "blocks", "Bad Block Management", 48,
            "Minimum number of valid blocks (NVB) 1004",
        ),
    }

    def offline_source_replay(_instructions, payload, _schema, **_kwargs):
        source_pages = {item["page"]: item["text"] for item in payload["pages"]}
        assert {4, 10, 48} <= set(source_pages)
        for _value, _unit, _condition, page, quote in facts.values():
            assert quote in source_pages[page]
        fields = []
        for key in expected:
            value = {
                "field_key": key, "value": None, "unit": None, "condition": None,
                "scope_type": "product_family", "scope_values": [], "status": "missing",
                "confidence": 0, "derived": False, "knowledge_type": "specification",
                "evidence": None, "conflict_evidence": [],
            }
            if key in facts:
                fact_value, unit, condition, page, quote = facts[key]
                value.update({
                    "value": fact_value, "unit": unit, "condition": condition,
                    "status": "found", "confidence": 1,
                    "evidence": {"source_id": payload["primary_source_id"], "page": page,
                                 "section": condition, "quote": quote},
                })
            fields.append(value)
        return {"fields": fields}, None, 0

    monkeypatch.setattr(ai, "_primary_or_secondary_extraction", offline_source_replay)
    monkeypatch.setattr(ai, "_run_critical_targeted_supplement",
                        lambda result, *_args, **_kwargs: result)
    imported = core.import_document(
        source_pdf.name, source_pdf.read_bytes(), "GigaDevice", "GD5F1GQ5xExxG",
        "NAND Flash",
    )
    assert {4, 10, 48} <= set(imported["analyzed_pages"])
    assert imported["model_calls"] == 0

    candidates = core.list_candidates(imported["device_id"])
    by_field = {item["canonical_name"]: item for item in candidates}
    for key, (value, _unit, _condition, page, quote) in facts.items():
        candidate = by_field[key]
        assert candidate["ai_value"] == value
        assert candidate["verify_status"] == "pending"
        assert candidate["source_page"] == page
        assert quote in candidate["source_text"]
        assert candidate["evidence"][0]["source_id"] == imported["source_id"]
        assert candidate["evidence"][0]["source_page"] == page
        assert quote in candidate["evidence"][0]["source_text"]

    client = TestClient(app)
    workbench = client.get(f"/api/product/devices/{imported['device_id']}/review-workbench")
    assert workbench.status_code == 200
    review_rows = {item["canonical_name"]: item for item in workbench.json()["rows"]}
    for key, (_value, _unit, _condition, page, quote) in facts.items():
        review_key = "ecc_requirement" if key == "ecc_capability" else key
        assert review_rows[review_key]["review_status"] == "UNREVIEWED"
        assert review_rows[review_key]["ai_value"] == _value
        assert review_rows[review_key]["evidence"][0]["source_page"] == page
        assert quote in review_rows[review_key]["evidence"][0]["source_text"]
    assert review_rows["ecc_observability"]["review_status"] == "NOT_REVIEWED"
