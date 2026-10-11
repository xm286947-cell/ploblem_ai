import json

from storage_life import ai, core, coverage, document_pipeline


def _missing_field(key):
    return {
        "field_key": key,
        "value": None,
        "unit": None,
        "condition": None,
        "scope_type": "product_family",
        "scope_values": [],
        "status": "missing",
        "confidence": 0.0,
        "derived": False,
        "knowledge_type": "specification",
        "evidence": None,
        "conflict_evidence": [],
    }


def test_gd5_missing_status_with_source_backed_value_becomes_review_candidate_not_fact():
    source_text = "P/E cycles with ECC: 100K\n4bits /528byte"
    _schema, expected = ai._single_pass_schema("NAND Flash")
    fields = [_missing_field(key) for key in expected]
    by_key = {item["field_key"]: item for item in fields}
    by_key["pe_cycles"].update({
        "value": "100K", "unit": "cycles", "condition": "with ECC",
        "evidence": {"source_id": "gd5", "page": 4, "section": "FEATURES",
                     "quote": "P/E cycles with ECC: 100K"},
    })
    by_key["ecc_capability"].update({
        "value": "4 bits / 528 bytes", "unit": "bits/bytes",
        "evidence": {"source_id": "gd5", "page": 4, "section": "FEATURES",
                     "quote": "4bits /528byte"},
    })

    adapted = ai._adapt_single_pass(
        {"fields": fields}, [(4, source_text, "text")], "NAND Flash", "GigaDevice", "GD5",
        "gd5", source_pages={"gd5": [(4, source_text, "text")]},
        covered_fields=set(expected), expected_fields=expected,
    )

    facts = {item["field_key"]: item for item in adapted["facts"]}
    candidates = {item["canonical_name"]: item for item in adapted["candidates"]}
    for key, expected_quote in (("pe_cycles", "P/E cycles with ECC: 100K"),
                                 ("ecc_capability", "4bits /528byte")):
        assert facts[key]["status"] == "missing"  # never auto-confirms a provider claim
        assert facts[key]["resolved_evidence"]
        assert expected_quote in facts[key]["resolved_evidence"]["quote"]
        assert candidates[key]["ai_value"]
        assert candidates[key]["source_page"] == 4
        assert candidates[key]["extraction_method"] == "agent_single_pass_status_mismatch"
        assert {"type": "status_value_mismatch", "field_key": key} in adapted["review_queue"]

    coverage_result = coverage.compute_coverage(
        device_type="NAND Flash", facts=adapted["facts"], searched_pages=[4],
        searched_fields={"pe_cycles": [4], "ecc_capability": [4]},
        expected_fields=["pe_cycles", "ecc_capability", "minimum_valid_blocks"],
    )
    states = {item["field_key"]: item for item in coverage_result["states"]}
    assert states["pe_cycles"]["state"] == coverage.UNRESOLVED
    assert states["pe_cycles"]["reason"] == "missing_status_has_value_or_evidence"
    assert states["ecc_capability"]["state"] == coverage.UNRESOLVED
    # NVB had no value/locator and its source page was outside this extraction run.
    assert "minimum_valid_blocks" not in candidates
    assert states["minimum_valid_blocks"]["state"] == coverage.UNRESOLVED


def test_missing_status_with_unresolvable_locator_does_not_create_candidate():
    _schema, expected = ai._single_pass_schema("NAND Flash")
    fields = [_missing_field(key) for key in expected]
    pe = next(item for item in fields if item["field_key"] == "pe_cycles")
    pe.update({
        "value": "100K", "unit": "cycles",
        "evidence": {"source_id": "gd5", "page": 48, "section": "NVB",
                     "quote": "P/E cycles with ECC: 100K"},
    })
    adapted = ai._adapt_single_pass(
        {"fields": fields}, [(4, "P/E cycles with ECC: 100K", "text")],
        "NAND Flash", "GigaDevice", "GD5", "gd5",
        source_pages={"gd5": [(4, "P/E cycles with ECC: 100K", "text")]},
        covered_fields=set(expected), expected_fields=expected,
    )
    assert not any(item["canonical_name"] == "pe_cycles" for item in adapted["candidates"])
    assert "pe_cycles" in adapted["unresolved_evidence"]


def test_missing_status_with_value_but_no_locator_is_reported_as_evidence_gap():
    _schema, expected = ai._single_pass_schema("NAND Flash")
    fields = [_missing_field(key) for key in expected]
    pe = next(item for item in fields if item["field_key"] == "pe_cycles")
    pe["value"] = "100K"
    adapted = ai._adapt_single_pass(
        {"fields": fields}, [(4, "P/E cycles with ECC: 100K", "text")],
        "NAND Flash", "GigaDevice", "GD5", "gd5",
        source_pages={"gd5": [(4, "P/E cycles with ECC: 100K", "text")]},
        covered_fields=set(expected), expected_fields=expected,
    )
    assert "pe_cycles" in adapted["unresolved_evidence"]
    assert not any(item["canonical_name"] == "pe_cycles" for item in adapted["candidates"])
    assert {"type": "status_value_mismatch", "field_key": "pe_cycles"} in adapted["review_queue"]
    assert {"type": "evidence_unresolved", "field_key": "pe_cycles"} in adapted["review_queue"]


def test_unread_relevant_page_keeps_missing_field_unresolved():
    pages = [
        (1, "GD5F1GQ5 DATASHEET\nFEATURES", "text"),
        (10, "ARRAY ORGANIZATION\n64 pages per block", "text"),
    ]
    scope = coverage.build_search_scope(pages, [1], "NAND Flash", "GigaDevice")
    assert "pages_per_block" in scope["incomplete_fields"]

    state = coverage.compute_coverage(
        device_type="NAND Flash",
        facts=[_missing_field("pages_per_block")],
        searched_pages=scope["searched_pages"],
        searched_fields=scope["searched_fields"],
        incomplete_fields=scope["incomplete_fields"],
        expected_fields=["pages_per_block"],
    )
    item = next(x for x in state["states"] if x["field_key"] == "pages_per_block")
    assert item["state"] == coverage.UNRESOLVED
    assert item["reason"] == "relevant_sections_not_fully_searched"


def test_complete_relevant_search_can_close_absence():
    pages = [
        (1, "GD5F1GQ5 DATASHEET\nFEATURES", "text"),
        (10, "ARRAY ORGANIZATION\nOrganization details", "text"),
    ]
    scope = coverage.build_search_scope(pages, [1, 10], "NAND Flash", "GigaDevice")
    assert "pages_per_block" not in scope["incomplete_fields"]

    state = coverage.compute_coverage(
        device_type="NAND Flash",
        facts=[_missing_field("pages_per_block")],
        searched_pages=scope["searched_pages"],
        searched_fields=scope["searched_fields"],
        incomplete_fields=scope["incomplete_fields"],
        expected_fields=["pages_per_block"],
    )
    item = next(x for x in state["states"] if x["field_key"] == "pages_per_block")
    assert item["state"] == coverage.NOT_SPECIFIED


def test_status_mismatch_candidates_are_persisted_pending_with_evidence(tmp_path, monkeypatch):
    source_text = "P/E cycles with ECC: 100K\n4bits /528byte"
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "handoff.sqlite3")
    monkeypatch.setattr(core, "extract_pdf", lambda _data: [(4, source_text, "text")])
    monkeypatch.setattr(core, "extract_pdf_pages", lambda _data, _pages: [(4, source_text, "text")])
    monkeypatch.setattr(document_pipeline, "build_markdown", lambda _data, _pages: (
        "# Page 4\n" + source_text, [(4, source_text, "text")],
        {"parser_version": "issue-618-test", "markdown_sha256": "fixture", "table_count": 0},
    ))
    monkeypatch.setattr(document_pipeline, "heuristic_identity", lambda *_args: {})
    monkeypatch.setattr(document_pipeline, "identity_key", lambda _identity, sha: sha)
    monkeypatch.setattr(ai, "configured", lambda: True)

    def offline_extraction(_pages, device_type, vendor, model, source_id=""):
        _schema, expected = ai._single_pass_schema(device_type)
        fields = [_missing_field(key) for key in expected]
        by_key = {item["field_key"]: item for item in fields}
        by_key["pe_cycles"].update({
            "value": "100K", "unit": "cycles", "condition": "with ECC",
            "evidence": {"source_id": source_id, "page": 4, "section": "FEATURES",
                         "quote": "P/E cycles with ECC: 100K"},
        })
        by_key["ecc_capability"].update({
            "value": "4 bits / 528 bytes",
            "evidence": {"source_id": source_id, "page": 4, "section": "FEATURES",
                         "quote": "4bits /528byte"},
        })
        result = ai._adapt_single_pass(
            {"fields": fields}, [(4, source_text, "text")], device_type, vendor, model,
            source_id, source_pages={source_id: [(4, source_text, "text")]},
            covered_fields=set(expected), expected_fields=expected,
        )
        result.update({
            "analyzed_pages": [4], "model_calls": 0,
            "searched_pages": [4],
            "searched_fields": {"pe_cycles": [4], "ecc_capability": [4]},
            "searched_sections": [],
        })
        result["coverage"] = coverage.compute_coverage(
            device_type=device_type, facts=result["facts"], searched_pages=[4],
            searched_fields=result["searched_fields"], expected_fields=expected,
        )
        result["coverage_layers"] = result["coverage"]["layers"]
        result["document_analysis"] = {"facts": result["facts"], "coverage": result["coverage"]}
        return result

    monkeypatch.setattr(ai, "extract_specification_once", offline_extraction)
    imported = core.import_document("GD5-fixture.pdf", b"%PDF-issue-618", "GigaDevice", "GD5", "NAND Flash")

    assert imported["candidate_count"] == 2
    candidates = {item["canonical_name"]: item for item in core.list_candidates(imported["device_id"])}
    assert set(candidates) == {"pe_cycles", "ecc_capability"}
    assert all(item["verify_status"] == "pending" for item in candidates.values())
    with core.connect() as con:
        evidence_count = con.execute(
            "SELECT COUNT(*) FROM candidate_evidence e JOIN candidates c ON c.id=e.candidate_id WHERE c.device_id=?",
            (imported["device_id"],),
        ).fetchone()[0]
        persisted_run = con.execute(
            "SELECT facts_json,review_queue_json,coverage_json FROM extraction_runs WHERE device_id=?",
            (imported["device_id"],),
        ).fetchone()
    assert evidence_count == 2
    assert persisted_run is not None
    persisted_facts = json.loads(persisted_run[0])
    persisted_queue = json.loads(persisted_run[1])
    persisted_coverage = json.loads(persisted_run[2])
    assert next(item for item in persisted_facts if item["field_key"] == "pe_cycles")["status"] == "missing"
    assert any(item["type"] == "status_value_mismatch" and item["field_key"] == "pe_cycles"
               for item in persisted_queue)
    assert next(item for item in persisted_coverage["states"] if item["field_key"] == "pe_cycles")["reason"] == \
        "missing_status_has_value_or_evidence"
