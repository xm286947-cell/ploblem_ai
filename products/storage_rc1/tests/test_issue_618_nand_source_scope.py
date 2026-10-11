"""#618 actual-source regression: critical-page reachability and per-part NAND rows.

No Provider calls, formal review, or Golden injection.
"""
from storage_life import ai, part_number_matrix, templates


PAGE2 = """SLC NAND | Product Brief | Rev.1.2
Part Number (24nm) Capacity (bit) VCC (V) Page Size (bit) Block Size (bit)
TC58NVG0S3HBAI4 1G 2.70 to 3.60 (2048+128)x8 (128K+8K)x8 -40 to 85 FBGA 63
TC58NVG2S0HBAI4 4G 2.70 to 3.60 (4096+256)x8 (256K+16K)x8 -40 to 85 FBGA 63
"""
P10 = """4 ARRAY ORGANIZATION
Table 3. Array Organization
Each device has Each block has Each page has
1024 x 64 64 - pages
1 block = (2K + 128)bytes x 64 pages
"""
P48 = """12.4 Assistant Bad Block Management
Table 12-6. Bad Block Mark information
Description Requirement
Minimum number of valid blocks (NVB) 1004
Total available blocks per die 1024
"""
P49 = """12.5 Block Protection
When an ERASE command is issued to a locked block, the erase failure status bit E_FAIL is set to 1.
When a PROGRAM command is issued to a locked block, the program failure status bit P_FAIL is set to 1.
"""
P43 = """Table 12-2. Status Register Bit Descriptions
ECC is enabled by default when device powered on.
"""
P46 = """Table 12-3. ECC Error Bits Descriptions
The NAND Flash device has an 8-bit status register.
No bit errors were detected during the previous read algorithm.
"""


def test_nand_critical_page_navigation_is_not_lost_to_56k_budget():
    pages = [(i, f"Unrelated source page {i} " + ("pad " * 1700), "markdown_pdf")
             for i in range(1, 52)]
    pages[9] = (10, P10, "markdown_pdf")
    pages[42] = (43, P43, "markdown_pdf")
    pages[45] = (46, P46, "markdown_pdf")
    pages[47] = (48, P48, "markdown_pdf")
    pages[48] = (49, P49, "markdown_pdf")
    picked = ai._single_pass_pages(pages, "NAND Flash", "GigaDevice", max_chars=56000)
    picked_numbers = {page for page, _text, _method in picked}
    assert {10, 43, 46, 48, 49} <= picked_numbers
    plan = {entry["page"]: entry for entry in templates.build_read_plan(pages, "NAND Flash", "GigaDevice")}
    assert "pages_per_block" in plan[10]["target_fields"]
    assert "minimum_valid_blocks" in plan[48]["target_fields"]
    assert {"erase_fail", "program_fail"} <= set(plan[49]["target_fields"])
    assert "internal_ecc" in plan[43]["target_fields"]
    assert {"ecc_status", "status_register"} <= set(plan[46]["target_fields"])


def test_kioxia_rows_do_not_merge_2k_4k_across_part_numbers():
    rows = part_number_matrix.source_scoped_nand_rows(
        [(2, PAGE2, "text")], source_id="source-kioxia", vendor="KIOXIA", device_type="NAND Flash"
    )
    assert len(rows) == 2
    by_part = {row["part"]: row for row in rows}
    assert (by_part["TC58NVG0S3HBAI4"]["capacity_gbit"],
            by_part["TC58NVG0S3HBAI4"]["page_data_bytes"],
            by_part["TC58NVG0S3HBAI4"]["page_spare_bytes"]) == (1, 2048, 128)
    assert (by_part["TC58NVG2S0HBAI4"]["capacity_gbit"],
            by_part["TC58NVG2S0HBAI4"]["page_data_bytes"],
            by_part["TC58NVG2S0HBAI4"]["page_spare_bytes"]) == (4, 4096, 256)
    candidates = ai._group_evidence(part_number_matrix.scoped_candidates(
        rows, field_labels=templates.fields_for("NAND Flash")
    ))
    by_key = {(item["canonical_name"], item["scope"]): item for item in candidates}
    assert len(by_key) == 4
    assert by_key[("page_size", "TC58NVG0S3HBAI4")]["ai_value"] == "2048"
    assert by_key[("page_size", "TC58NVG2S0HBAI4")]["ai_value"] == "4096"
    for item in candidates:
        assert item["source_page"] == 2
        assert item["scope"] in item["source_text"]
        assert item["extraction_method"] == "source_table_part_number"


def test_part_row_without_exact_source_scope_or_with_conflicting_duplicate_fails_closed():
    body = """Part Number Capacity (bit) Page Size (bit)
TC58NVG0S3HBAI4 1G (2048+128)x8
TC58NVG0S3HBAI4 1G (4096+256)x8
TC58NVG2S0HBAI4 4G not specified
"""
    assert part_number_matrix.source_scoped_nand_rows(
        [(2, body, "text")], source_id="kioxia", vendor="KIOXIA", device_type="NAND Flash"
    ) == []
    assert part_number_matrix.source_scoped_nand_rows(
        [(2, PAGE2, "text")], source_id="x", vendor="GigaDevice", device_type="NAND Flash"
    ) == []


def test_actual_single_pass_merge_replaces_family_values_with_scoped_pending_rows(monkeypatch):
    _schema, expected = ai._single_pass_schema("NAND Flash")
    fields = [{
        "field_key": key, "value": None, "unit": None, "condition": None,
        "scope_type": "product_family", "scope_values": [], "status": "missing",
        "confidence": 0, "derived": False, "knowledge_type": "specification",
        "evidence": None, "conflict_evidence": [],
    } for key in expected]
    # Simulate the problematic existing model one-value family summary.
    for f in fields:
        if f["field_key"] in {"page_size", "capacity"}:
            f.update({
                "value": "2048" if f["field_key"] == "page_size" else "1",
                "unit": "bytes" if f["field_key"] == "page_size" else "Gbit",
                "status": "found",
                "evidence": {"source_id": "kioxia", "page": 2, "section": "Part Number",
                             "quote": "TC58NVG0S3HBAI4 1G 2.70 to 3.60 (2048+128)x8"},
            })
    def offline(_instructions, _payload, _schema, **_kwargs):
        return {"fields": fields}, None, 0
    monkeypatch.setattr(ai, "_primary_or_secondary_extraction", offline)
    monkeypatch.setattr(ai, "_run_critical_targeted_supplement", lambda result, *_args, **_kwargs: result)
    adapted = ai.extract_specification_bundle_once(
        [{"source_id": "kioxia", "pages": [(2, PAGE2, "text")]}],
        "NAND Flash", "KIOXIA", "SLC NAND Product Brief",
    )
    candidates = adapted["candidates"]
    for name in ("capacity", "page_size"):
        items = [item for item in candidates if item["canonical_name"] == name]
        assert len(items) == 2
        assert {item["scope"] for item in items} == {"TC58NVG0S3HBAI4", "TC58NVG2S0HBAI4"}
        state = next(item for item in adapted["coverage"]["states"] if item["field_key"] == name)
        assert state["state"] == "UNRESOLVED"
        assert state["reason"] == "part_number_variants_require_scope"
    assert adapted["part_number_matrix"]["status"] == "PENDING_PART_NUMBER_REVIEW"
    # Family/Model UI reads the document_models table. Source-backed rows must
    # register orderable models, not just produce candidate.scope strings.
    discovered = {x["value"]: x for x in adapted["models"]}
    assert set(discovered) == {"TC58NVG0S3HBAI4", "TC58NVG2S0HBAI4"}
    assert all(x["scope"] == x["value"] and x["page"] == 2 and x["quote"]
               for x in discovered.values())
    assert adapted["model_calls"] == 0


def test_confirmed_part_number_values_cannot_be_exposed_as_family_device_fact(monkeypatch):
    from storage_life import product_api
    monkeypatch.setattr(product_api.core, "list_devices", lambda: [
        {"id": "family", "vendor": "KIOXIA", "model": "SLC NAND", "device_type": "NAND Flash"}
    ])
    monkeypatch.setattr(product_api, "_coverage_states", lambda _id: {})
    monkeypatch.setattr(product_api, "_candidate_map", lambda _id: {
        "page_size": [
            {"id": "a", "canonical_name": "page_size", "ai_value": "2048",
             "final_value": "2048", "final_unit": "bytes", "ai_unit": "bytes",
             "scope": "TC58NVG0S3HBAI4", "verify_status": "confirmed",
             "extraction_method": "source_table_part_number", "evidence": []},
            {"id": "b", "canonical_name": "page_size", "ai_value": "4096",
             "final_value": "4096", "final_unit": "bytes", "ai_unit": "bytes",
             "scope": "TC58NVG2S0HBAI4", "verify_status": "confirmed",
             "extraction_method": "source_table_part_number", "evidence": []},
        ]
    })
    monkeypatch.setattr(product_api.parameter_baseline, "product_fields", lambda *_: [
        {"canonical_name": "page_size", "parameter_name": "Page Size"}
    ])
    monkeypatch.setattr(product_api, "_enrich_evidence", lambda _device, evidence: evidence)
    monkeypatch.setattr(product_api, "_device_lifecycle", lambda _id: {})
    monkeypatch.setattr(product_api.core, "specification_workflow_status", lambda _id: {})
    monkeypatch.setattr(product_api.core, "get_device_conclusion", lambda _id: {})
    result = product_api.device_slots("family")
    assert result["slots"][0]["status"] == "AMBIGUOUS"
    assert result["slots"][0]["value"] is None
    assert result["device_facts"] == []


def test_multi_part_family_cannot_become_formal_ready_even_after_all_rows_confirmed(monkeypatch):
    from storage_life import core

    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def execute(self, *_args):
            return self
        def fetchone(self):
            return {"device_type": "NAND Flash"}

    monkeypatch.setattr(core, "connect", lambda: Connection())
    monkeypatch.setattr(core, "_critical_fields_for", lambda _type, _items: [])
    monkeypatch.setattr(core, "get_extraction_run", lambda _id: None)
    monkeypatch.setattr(core, "get_final_review", lambda _id: None)
    specs = [{
        "canonical_name": "page_size", "value": value, "unit": "bytes",
        "scope": part, "priority": "P0", "review_status": "confirmed",
        "evidence": [{"extraction_method": "source_table_part_number", "scope": part}]
    } for part, value in (
        ("TC58NVG0S3HBAI4", "2048"), ("TC58NVG2S0HBAI4", "4096")
    )]
    result = core.specification_workflow_status("family", specs=specs)
    assert result["formal_ready"] is False
    assert result["status"] == "attention_required"
    assert result["part_number_scope_required_fields"]


def test_existing_family_matrix_binds_scoped_candidates_to_exact_orderable_models(monkeypatch):
    from storage_life import core

    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def execute(self, *_args):
            return self
        def fetchone(self):
            return {"id": "family", "vendor": "KIOXIA",
                    "model": "SLC NAND", "device_type": "NAND Flash"}

    monkeypatch.setattr(core, "connect", lambda: Connection())
    rows = part_number_matrix.source_scoped_nand_rows(
        [(2, PAGE2, "text")], source_id="source-kioxia", vendor="KIOXIA",
        device_type="NAND Flash",
    )
    candidates = part_number_matrix.scoped_candidates(
        rows, field_labels=templates.fields_for("NAND Flash")
    )
    for i, candidate in enumerate(candidates):
        candidate.update({
            "id": "candidate-" + str(i), "verify_status": "pending",
            "final_value": None, "final_unit": None,
        })
    models = [
        {"id": "model-" + str(i), "ai_model": row["part"],
         "scope": row["part"], "verify_status": "pending"}
        for i, row in enumerate(rows)
    ]
    monkeypatch.setattr(core, "list_models", lambda _id: models)
    monkeypatch.setattr(core, "list_candidates", lambda _id: candidates)
    monkeypatch.setattr(core, "get_document_identity", lambda _id: None)
    monkeypatch.setattr(core, "get_extraction_run", lambda _id: None)
    monkeypatch.setattr(core, "get_device_conclusion", lambda _id: None)
    monkeypatch.setattr(core, "specification_workflow_status", lambda _id: {
        "status": "pending_confirmation", "formal_ready": False,
    })
    view = core.family_view("family")
    assert view["counts"]["models"] == 2
    assert view["counts"]["variant_specs"] == 4
    assert view["counts"]["unbound_specs"] == 0
    assert view["counts"]["common_specs"] == 0
    models_by_part = {x["model"]: x["id"] for x in view["models"]}
    page_row = next(row for row in view["matrix_rows"]
                    if row["canonical_name"] == "page_size")
    assert page_row["cells"][models_by_part["TC58NVG0S3HBAI4"]][0]["value"] == "2048"
    assert page_row["cells"][models_by_part["TC58NVG2S0HBAI4"]][0]["value"] == "4096"
    assert all(cell[0]["verify_status"] == "pending"
               for cell in page_row["cells"].values())
