from storage_life import coverage, core


def _fact(field, *, status="found", value="x", page=2, quote="supported value"):
    return {
        "field_key": field,
        "status": status,
        "value": value,
        "resolved_evidence": {"page": page, "quote": quote} if quote else None,
        "evidence": [{"page": page, "quote": quote}] if quote else [],
    }


def test_found_requires_value_valid_evidence_and_analyzed_page():
    result = coverage.compute_coverage(
        device_type="NOR Flash",
        facts=[_fact("pe_cycles")],
        searched_pages=[2],
        searched_fields={"pe_cycles": [2]},
        expected_fields=["pe_cycles"],
    )
    state = next(x for x in result["states"] if x["field_key"] == "pe_cycles")
    assert state["state"] == coverage.FOUND

    outside = coverage.compute_coverage(
        device_type="NOR Flash",
        facts=[_fact("pe_cycles", page=9)],
        searched_pages=[2],
        searched_fields={"pe_cycles": [2]},
        expected_fields=["pe_cycles"],
    )
    assert next(x for x in outside["states"] if x["field_key"] == "pe_cycles")["state"] == coverage.UNRESOLVED


def test_missing_is_not_specified_only_when_relevant_section_was_searched():
    searched = coverage.compute_coverage(
        device_type="NOR Flash", facts=[], searched_pages=[7],
        searched_fields={"retention": [7]}, expected_fields=["retention"],
    )
    unsearched = coverage.compute_coverage(
        device_type="NOR Flash", facts=[], searched_pages=[7],
        searched_fields={}, expected_fields=["retention"],
    )
    assert next(x for x in searched["states"] if x["field_key"] == "retention")["state"] == coverage.NOT_SPECIFIED
    assert next(x for x in unsearched["states"] if x["field_key"] == "retention")["state"] == coverage.UNRESOLVED


def test_profile_excludes_cross_device_fields_without_missing_penalty():
    result = coverage.compute_coverage(
        device_type="NOR Flash", facts=[], searched_pages=[], searched_fields={},
        expected_fields=["pe_cycles", "retention"],
    )
    states = {x["field_key"]: x["state"] for x in result["states"]}
    assert states["tbw"] == coverage.NOT_APPLICABLE
    assert states["life_time_a"] == coverage.NOT_APPLICABLE
    assert "tbw" not in result["layers"]["general"]["unresolved_fields"]
    assert "life_time_a" not in result["layers"]["general"]["unresolved_fields"]


def test_device_profiles_do_not_leak_device_specific_fields():
    assert "tbw" not in coverage.profile_fields("NOR Flash")
    assert "life_time_a" not in coverage.profile_fields("SSD")
    assert "read_retry" not in coverage.profile_fields("eMMC")


def test_coverage_is_layered_and_critical_state_is_independent():
    result = coverage.compute_coverage(
        device_type="NOR Flash",
        facts=[_fact("manufacturer"), _fact("pe_cycles")],
        searched_pages=[2],
        searched_fields={"manufacturer": [2], "pe_cycles": [2], "retention": [2]},
        expected_fields=["manufacturer", "pe_cycles", "retention", "status_register"],
    )
    assert set(result["layers"]) == {"identity", "critical", "diagnostic", "general"}
    assert result["layers"]["critical"]["counts"][coverage.FOUND] == 1
    assert result["layers"]["critical"]["counts"][coverage.NOT_SPECIFIED] == 1
    assert result["layers"]["critical"]["complete"] is True
    assert result["layers"]["diagnostic"]["complete"] is False


def test_search_scope_records_actual_pages_and_semantic_sections_only():
    pages = [
        (1, "GD25Q64E DATASHEET\nFEATURES", "text"),
        (5, "ORDERING INFORMATION\nGD25Q64Exx", "text"),
        (9, "unrelated appendix", "text"),
    ]
    scope = coverage.build_search_scope(pages, [1, 5], "NOR Flash", "GigaDevice")
    assert scope["searched_pages"] == [1, 5]
    assert 5 in scope["searched_fields"]["covered_part_numbers"]
    assert all(item["page"] in {1, 5} for item in scope["searched_sections"])
    assert not any(item["page"] == 9 for item in scope["searched_sections"])


def test_extraction_run_migrates_and_round_trips_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "coverage.sqlite3")
    with core.connect() as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(extraction_runs)")}
    assert {
        "searched_pages_json", "searched_sections_json", "searched_fields_json",
        "coverage_json", "coverage_layers_json", "document_analysis_json",
    } <= columns
