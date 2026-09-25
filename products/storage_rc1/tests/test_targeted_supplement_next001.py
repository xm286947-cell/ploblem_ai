from storage_life import ai


def _contract(device_type, found=None):
    found = found or {}
    _, keys = ai._single_pass_schema(device_type)
    fields = []
    for key in keys:
        item = {
            "field_key": key,
            "value": None,
            "unit": None,
            "condition": None,
            "scope_type": "product_family",
            "scope_values": [],
            "evidence": None,
            "conflict_evidence": [],
            "confidence": 0,
            "status": "missing",
            "derived": False,
            "knowledge_type": "specification",
        }
        item.update(found.get(key) or {})
        fields.append(item)
    return {"fields": fields}


def _subset_contract(fields):
    return {"fields": fields}


def test_critical_unresolved_gets_one_targeted_supplement_and_recomputes_coverage(monkeypatch):
    pages = [
        (1, "FEATURES\nEndurance: 100,000 P/E cycles\nData Retention: 20 years", "markdown_text"),
        (2, "MEMORY ORGANIZATION\nPage size 256 bytes", "markdown_text"),
    ]
    initial = _contract("NOR Flash", {
        "manufacturer": {
            "value": "Example", "status": "found", "confidence": .99,
            "evidence": {"source_id": "pdf", "page": 1, "section": "cover", "quote": "FEATURES"},
        },
        "product_family": {
            "value": "N25", "status": "found", "confidence": .99,
            "evidence": {"source_id": "pdf", "page": 1, "section": "cover", "quote": "FEATURES"},
        },
        "pe_cycles": {
            "value": "100000", "unit": "cycles", "status": "found", "confidence": .95,
            "evidence": {"source_id": "pdf", "page": 1, "section": "FEATURES", "quote": "100,000 P/E cycles"},
        },
        # A found claim whose evidence page was never supplied must remain UNRESOLVED.
        "retention": {
            "value": "20", "unit": "years", "status": "found", "confidence": .95,
            "evidence": {"source_id": "pdf", "page": 99, "section": "FEATURES", "quote": "Data Retention: 20 years"},
        },
    })
    supplement = _subset_contract([{
        "field_key": "retention", "value": "20", "unit": "years", "condition": None,
        "scope_type": "product_family", "scope_values": [],
        "evidence": {"source_id": "pdf", "page": 1, "section": "FEATURES", "quote": "Data Retention: 20 years"},
        "conflict_evidence": [], "confidence": .97, "status": "found", "derived": False,
        "knowledge_type": "specification",
    }])
    calls = []

    def fake_call(instructions, payload, schema, client=None):
        calls.append({"payload": payload, "schema": schema})
        return initial if len(calls) == 1 else supplement

    monkeypatch.setattr(ai, "_call", fake_call)
    result = ai.extract_specification_bundle_once(
        [{"source_id": "pdf", "pages": pages}], "NOR Flash", "Example", "N25"
    )

    assert len(calls) == 2
    assert result["model_calls"] == 2
    assert result["supplement_rounds"] == 1
    assert result["supplement_status"] == "completed"
    assert result["supplement_target_fields"] == ["retention"]
    assert calls[1]["payload"]["operation"] == "critical_unresolved_targeted_supplement"
    assert calls[1]["payload"]["target_fields"] == ["retention"]
    assert calls[1]["schema"]["properties"]["fields"]["items"]["properties"]["field_key"]["enum"] == ["retention"]
    state = next(x for x in result["coverage"]["states"] if x["field_key"] == "retention")
    assert state["state"] == "FOUND"
    assert result["coverage"]["critical_unresolved"] == []
    retention = next(x for x in result["candidates"] if x["canonical_name"] == "retention")
    assert retention["source_page"] == 1
    assert "Data Retention: 20 years" in retention["source_text"]


def test_general_unresolved_does_not_trigger_targeted_supplement(monkeypatch):
    pages = [(1, "FEATURES\nTBW: 600 TB\nSMART Health supported", "markdown_text")]
    initial = _contract("SSD", {
        "tbw": {
            "value": "600", "unit": "TB", "status": "found", "confidence": .95,
            "evidence": {"source_id": "pdf", "page": 1, "section": "FEATURES", "quote": "TBW: 600 TB"},
        },
        "smart_health": {
            "value": "Supported", "status": "found", "confidence": .95,
            "evidence": {"source_id": "pdf", "page": 1, "section": "FEATURES", "quote": "SMART Health supported"},
        },
    })
    calls = []

    def fake_call(instructions, payload, schema, client=None):
        calls.append(payload)
        return initial

    monkeypatch.setattr(ai, "_call", fake_call)
    result = ai.extract_specification_bundle_once(
        [{"source_id": "pdf", "pages": pages}], "SSD", "Example", "S1"
    )
    assert len(calls) == 1
    assert result["model_calls"] == 1
    assert result["supplement_rounds"] == 0
    assert result["supplement_target_fields"] == []


def test_targeted_supplement_never_recurses_past_one_round(monkeypatch):
    pages = [(1, "FEATURES\nEndurance section\nRetention is unclear", "markdown_text")]
    initial = _contract("NOR Flash", {
        "pe_cycles": {
            "value": "100000", "unit": "cycles", "status": "found", "confidence": .9,
            "evidence": {"source_id": "pdf", "page": 1, "section": "FEATURES", "quote": "Endurance section"},
        },
        "retention": {
            "value": "20", "unit": "years", "status": "found", "confidence": .5,
            "evidence": {"source_id": "pdf", "page": 88, "section": "FEATURES", "quote": "Retention is unclear"},
        },
    })
    supplement = _subset_contract([{
        "field_key": "retention", "value": None, "unit": None, "condition": None,
        "scope_type": "product_family", "scope_values": [], "evidence": None,
        "conflict_evidence": [], "confidence": .2, "status": "ambiguous", "derived": False,
        "knowledge_type": "specification",
    }])
    calls = []

    def fake_call(instructions, payload, schema, client=None):
        calls.append(payload)
        if len(calls) > 2:
            raise AssertionError("supplement recursed beyond MAX_SUPPLEMENT_ROUNDS")
        return initial if len(calls) == 1 else supplement

    monkeypatch.setattr(ai, "_call", fake_call)
    result = ai.extract_specification_bundle_once(
        [{"source_id": "pdf", "pages": pages}], "NOR Flash", "Example", "N25"
    )
    assert len(calls) == 2
    assert result["supplement_rounds"] == ai.MAX_SUPPLEMENT_ROUNDS == 1
    assert result["coverage"]["critical_unresolved"] == ["retention"]


def test_no_semantic_target_pages_keeps_unresolved_without_second_call(monkeypatch):
    base = {
        "facts": [], "candidates": [], "expected_fields": ["retention"],
        "searched_pages": [], "searched_sections": [], "searched_fields": {},
        "unresolved_evidence": [], "review_queue": [], "review_required": False,
        "coverage": {"critical_unresolved": ["retention"]}, "model_calls": 1,
    }
    monkeypatch.setattr(ai.templates, "build_read_plan", lambda *args, **kwargs: [])
    monkeypatch.setattr(ai, "_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model must not be called")))
    result = ai._run_critical_targeted_supplement(
        base, [{"source_id": "pdf", "pages": [(1, "random appendix", "text")]}],
        "NOR Flash", "Example", "N25"
    )
    assert result["supplement_rounds"] == 0
    assert result["supplement_status"] == "no_target_pages"
    assert result["coverage"]["critical_unresolved"] == ["retention"]


def test_emmc_runtime_targeted_supplement_stays_on_parameter_extract_agent(monkeypatch):
    from storage_life import runtime_bridge

    pages = [(1, "HEALTH REPORT\nDEVICE_LIFE_TIME_EST_TYP_A", "markdown_text")]
    base = {
        "facts": [], "candidates": [], "expected_fields": ["device_life_time_est_typ_a"],
        "searched_pages": [], "searched_sections": [], "searched_fields": {},
        "unresolved_evidence": [], "review_queue": [], "review_required": False,
        "coverage": {"critical_unresolved": ["device_life_time_est_typ_a"]}, "model_calls": 1,
    }
    response = _subset_contract([{
        "field_key": "device_life_time_est_typ_a", "value": "0x03", "unit": None, "condition": None,
        "scope_type": "product_family", "scope_values": [],
        "evidence": {"source_id": "pdf", "page": 1, "section": "HEALTH REPORT", "quote": "DEVICE_LIFE_TIME_EST_TYP_A"},
        "conflict_evidence": [], "confidence": .9, "status": "found", "derived": False,
        "knowledge_type": "diagnostic_capability",
    }])
    calls = {"parameter": 0, "generic": 0}

    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    monkeypatch.setattr(runtime_bridge, "call_parameter_extract", lambda *args, **kwargs: (calls.__setitem__("parameter", calls["parameter"] + 1) or response))
    monkeypatch.setattr(runtime_bridge, "call_json", lambda *args, **kwargs: (calls.__setitem__("generic", calls["generic"] + 1) or response))

    result = ai._run_critical_targeted_supplement(
        base, [{"source_id": "pdf", "pages": pages}], "eMMC", "Example", "E1"
    )
    assert calls == {"parameter": 1, "generic": 0}
    assert result["model_calls"] == 2
    assert result["supplement_rounds"] == 1
