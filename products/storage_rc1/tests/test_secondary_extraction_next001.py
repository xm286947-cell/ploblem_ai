from __future__ import annotations

import hashlib

import pytest

from storage_life import ai, runtime_bridge


PAGES = [
    (
        1,
        """GigaDevice Semiconductor Inc.\nGD25Q64E 64M-bit Serial Flash\nFEATURES\nMinimum 100,000 Program/Erase Cycles\n20-year data retention typical\nVALID PART NUMBERS\nGD25Q64EBIG\n""",
        "markdown_text",
    )
]


def _item(key, *, value=None, status="missing", quote=None, unit=None, scope_values=None):
    evidence = None
    if quote is not None:
        evidence = {"source_id": "pdf", "page": 1, "section": "FEATURES", "quote": quote}
    return {
        "field_key": key,
        "value": value,
        "unit": unit,
        "condition": None,
        "scope_type": "part_number" if scope_values else "product_family",
        "scope_values": list(scope_values or []),
        "evidence": evidence,
        "conflict_evidence": [],
        "confidence": 0.99 if status == "found" else 0.0,
        "status": status,
        "derived": False,
        "knowledge_type": "specification",
    }


def _contract(*, retention_status="found"):
    _schema, keys = ai._single_pass_schema("NOR Flash")
    found = {
        "manufacturer": _item(
            "manufacturer", value="GigaDevice Semiconductor Inc.", status="found",
            quote="GigaDevice Semiconductor Inc.",
        ),
        "product_family": _item(
            "product_family", value="GD25Q64E", status="found", quote="GD25Q64E 64M-bit Serial Flash",
        ),
        "covered_part_numbers": _item(
            "covered_part_numbers", value="Valid Part Numbers", status="found",
            quote="GD25Q64EBIG", scope_values=["GD25Q64EBIG"],
        ),
        "pe_cycles": _item(
            "pe_cycles", value="100000", unit="cycles", status="found",
            quote="Minimum 100,000 Program/Erase Cycles",
        ),
    }
    if retention_status == "found":
        found["retention"] = _item(
            "retention", value="20", unit="years", status="found",
            quote="20-year data retention typical",
        )
    else:
        found["retention"] = _item("retention", value=None, status=retention_status)
    return {"fields": [found.get(key) or _item(key) for key in keys]}


def _handoff_error(content: str):
    return runtime_bridge.RuntimeBridgeCallError(
        "provider content requires semantic repair",
        code="SEMANTIC_REPAIR_REQUIRED",
        category="VALIDATION",
        retryable=True,
        details={
            "semantic_handoff": {
                "recoverable_content_available": True,
                "content_ref": "mock://provider-content/1",
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "content_length": len(content),
                "raw_finish_reason": "stop",
                "raw_usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
                "request_max_tokens": 8192,
                "request_max_completion_tokens": "NOT_SENT",
                "structured_output_capability": "UNKNOWN",
                "structured_output_request": "NONE",
                "response_format_type": "NOT_SENT",
            }
        },
    )


def _run(monkeypatch, call_json):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    monkeypatch.setattr(ai, "configured", lambda: True)
    monkeypatch.setattr(runtime_bridge, "call_json", call_json)
    return ai.extract_specification_bundle_once(
        [{"source_id": "pdf", "pages": PAGES}], "NOR Flash", "GigaDevice", "GD25Q64E"
    )


def test_a_primary_strict_json_pass_does_not_trigger_secondary(monkeypatch):
    calls = []

    def call_json(_instructions, payload, _schema):
        calls.append(payload.get("operation") or "primary")
        return _contract()

    monkeypatch.setattr(
        runtime_bridge,
        "resolve_semantic_repair_content",
        lambda _exc: (_ for _ in ()).throw(AssertionError("secondary resolver must not run")),
    )
    result = _run(monkeypatch, call_json)
    assert calls == ["primary"]
    assert result["secondary_extraction"]["status"] == "not_required"
    assert result["model_calls"] == 1
    assert result["coverage"]["critical_unresolved"] == []


def test_b_runtime_wrapper_recovery_is_transparent_to_storage(monkeypatch):
    # Runtime owns wrapper recovery; Storage must see the same normal structured result
    # and must not run any local Markdown stripping/JSON repair path.
    calls = []

    def call_json(_instructions, payload, _schema):
        calls.append(payload.get("operation") or "primary")
        return _contract()

    result = _run(monkeypatch, call_json)
    assert calls == ["primary"]
    assert result["secondary_extraction"]["status"] == "not_required"


def test_c_useful_invalid_json_handoff_runs_one_secondary_extraction(monkeypatch):
    malformed = (
        '{"fields":[{"field_key":"manufacturer","value":"GigaDevice Semiconductor Inc."},'
        '{"field_key":"pe_cycles","value":"100000"},'
        '{"field_key":"retention","value":"20"}'
    )
    calls = []

    def call_json(_instructions, payload, _schema):
        calls.append(payload)
        if len(calls) == 1:
            raise _handoff_error(malformed)
        assert payload["operation"] == "secondary_structured_extraction"
        assert payload["trigger"] == "SEMANTIC_REPAIR_REQUIRED"
        assert payload["previous_provider_text"] == malformed
        assert payload["device_type"] == "NOR Flash"
        assert payload["lifetime_profile"]["operation_model"]
        return _contract()

    monkeypatch.setattr(runtime_bridge, "resolve_semantic_repair_content", lambda _exc: malformed)
    result = _run(monkeypatch, call_json)
    assert len(calls) == 2
    assert result["model_calls"] == 2
    assert result["secondary_extraction"]["status"] == "completed"
    assert result["secondary_extraction"]["trigger"] == "SEMANTIC_REPAIR_REQUIRED"
    assert "content_ref" not in result["secondary_extraction"]
    assert result["coverage"]["critical_unresolved"] == []
    states = {x["field_key"]: x["state"] for x in result["coverage"]["states"]}
    assert states["pe_cycles"] == "FOUND"
    assert states["retention"] == "FOUND"


def test_d_secondary_absence_stays_unresolved_then_targeted_supplement(monkeypatch):
    malformed = '{"fields":[{"field_key":"pe_cycles","value":"100000"}'
    calls = []

    def call_json(_instructions, payload, _schema):
        calls.append(payload)
        if len(calls) == 1:
            raise _handoff_error(malformed)
        if len(calls) == 2:
            assert payload["operation"] == "secondary_structured_extraction"
            # Missing from the failed Provider text is deliberately ambiguous, not NOT_SPECIFIED.
            return _contract(retention_status="ambiguous")
        assert payload["operation"] == "critical_unresolved_targeted_supplement"
        assert payload["target_fields"] == ["retention"]
        return {"fields": [_item(
            "retention", value="20", unit="years", status="found",
            quote="20-year data retention typical",
        )]}

    monkeypatch.setattr(runtime_bridge, "resolve_semantic_repair_content", lambda _exc: malformed)
    result = _run(monkeypatch, call_json)
    assert len(calls) == 3
    assert result["model_calls"] == 3
    assert result["secondary_extraction"]["status"] == "completed"
    assert result["supplement_rounds"] == 1
    assert result["supplement_target_fields"] == ["retention"]
    assert result["coverage"]["critical_unresolved"] == []


def test_e_secondary_extraction_failure_remains_fail_closed(monkeypatch):
    malformed = '{"fields":[{"field_key":"pe_cycles","value":"100000"}'
    calls = []

    def call_json(_instructions, payload, _schema):
        calls.append(payload)
        if len(calls) == 1:
            raise _handoff_error(malformed)
        raise runtime_bridge.RuntimeBridgeCallError(
            "secondary output also requires semantic repair",
            code="SEMANTIC_REPAIR_REQUIRED",
            category="VALIDATION",
            retryable=True,
        )

    monkeypatch.setattr(runtime_bridge, "resolve_semantic_repair_content", lambda _exc: malformed)
    with pytest.raises(ai.AIResponseError, match="SEMANTIC_REPAIR_REQUIRED"):
        _run(monkeypatch, call_json)
    assert len(calls) > 2
    assert any(x.get("operation") == "secondary_structured_extraction_partition" for x in calls[2:])


def test_e2_secondary_semantic_failure_uses_partitioned_rescue(monkeypatch):
    malformed = '{"fields":[{"field_key":"pe_cycles","value":"100000"}'
    calls = []
    full = _contract()
    by_key = {item["field_key"]: item for item in full["fields"]}

    def call_json(_instructions, payload, _schema):
        calls.append(payload)
        if len(calls) == 1:
            raise _handoff_error(malformed)
        if len(calls) == 2:
            raise runtime_bridge.RuntimeBridgeCallError(
                "secondary output also requires semantic repair",
                code="SEMANTIC_REPAIR_REQUIRED",
                category="VALIDATION",
                retryable=True,
            )
        assert payload["operation"] == "secondary_structured_extraction_partition"
        requested = payload["target_fields"]
        return {"fields": [by_key[key] for key in requested]}

    monkeypatch.setattr(runtime_bridge, "resolve_semantic_repair_content", lambda _exc: malformed)
    result = _run(monkeypatch, call_json)
    groups = ai._secondary_partition_groups("NOR Flash")
    assert len(calls) == 2 + len(groups)
    assert result["secondary_extraction"]["status"] == "completed_partitioned"
    assert result["secondary_extraction"]["successful_groups"] == len(groups)
    assert result["secondary_extraction"]["failed_groups"] == []
    assert result["coverage"]["critical_unresolved"] == []
    assert result["model_calls"] == 2 + len(groups)


def test_handoff_hash_mismatch_fails_before_second_model_call(monkeypatch):
    malformed = '{"fields":['
    calls = []

    def call_json(_instructions, payload, _schema):
        calls.append(payload)
        raise _handoff_error(malformed)

    monkeypatch.setattr(runtime_bridge, "resolve_semantic_repair_content", lambda _exc: malformed + "tampered")
    with pytest.raises(ai.AIResponseError, match="哈希校验失败"):
        _run(monkeypatch, call_json)
    assert len(calls) == 1


def test_generic_runtime_call_semantic_handoff_runs_one_secondary_pass(monkeypatch):
    malformed = 'vendor=GigaDevice; model=GD25Q64E; device_type=NOR Flash; json broken'
    calls = []

    def call_json(_instructions, payload, _schema):
        calls.append(payload)
        if len(calls) == 1:
            raise _handoff_error(malformed)
        assert payload["operation"] == "secondary_structured_extraction"
        assert payload["trigger"] == "SEMANTIC_REPAIR_REQUIRED"
        assert payload["previous_provider_text"] == malformed
        assert "pages" not in payload
        return {
            "vendor": {"value": "GigaDevice", "page": 1, "quote": "GigaDevice Semiconductor Inc.", "confidence": 0.99},
            "model": {"value": "GD25Q64E", "page": 1, "quote": "GD25Q64E 64M-bit Serial Flash", "confidence": 0.99},
            "device_type": {"value": "NOR Flash", "page": 1, "quote": "Serial Flash", "confidence": 0.99},
        }

    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    monkeypatch.setattr(ai, "configured", lambda: True)
    monkeypatch.setattr(runtime_bridge, "call_json", call_json)
    monkeypatch.setattr(runtime_bridge, "resolve_semantic_repair_content", lambda _exc: malformed)

    result = ai.identify_device(PAGES)
    assert len(calls) == 2
    assert result["vendor"]["value"] == "GigaDevice"
    assert result["model"]["value"] == "GD25Q64E"
    assert result["device_type"]["value"] == "NOR Flash"


def test_generic_runtime_call_second_semantic_failure_stays_fail_closed(monkeypatch):
    malformed = 'vendor=GigaDevice; model=GD25Q64E; device_type=NOR Flash; json broken'
    calls = []

    def call_json(_instructions, payload, _schema):
        calls.append(payload)
        if len(calls) == 1:
            raise _handoff_error(malformed)
        raise runtime_bridge.RuntimeBridgeCallError(
            "provider content requires semantic repair",
            code="SEMANTIC_REPAIR_REQUIRED",
            category="VALIDATION",
            retryable=True,
        )

    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    monkeypatch.setattr(ai, "configured", lambda: True)
    monkeypatch.setattr(runtime_bridge, "call_json", call_json)
    monkeypatch.setattr(runtime_bridge, "resolve_semantic_repair_content", lambda _exc: malformed)

    with pytest.raises(ai.AIResponseError, match="SEMANTIC_REPAIR_REQUIRED"):
        ai.identify_device(PAGES)
    assert len(calls) == 2


def test_targeted_supplement_second_semantic_failure_uses_small_field_rescue(monkeypatch):
    """A semantic failure inside Targeted Supplement must not recreate the field issue."""
    malformed = 'retention=20 years; page=1; quote="20-year data retention typical"; malformed json'
    calls = []

    def call_json(_instructions, payload, _schema):
        calls.append(payload)
        # Primary business extraction succeeds but leaves the critical retention fact unresolved.
        if len(calls) == 1:
            return _contract(retention_status="ambiguous")
        # Targeted Supplement's primary Runtime result is semantically damaged.
        if len(calls) == 2:
            assert payload["operation"] == "critical_unresolved_targeted_supplement"
            raise _handoff_error(malformed)
        # Its normal Secondary Extraction is also semantically damaged.
        if len(calls) == 3:
            assert payload["operation"] == "secondary_structured_extraction"
            raise runtime_bridge.RuntimeBridgeCallError(
                "provider content requires semantic repair",
                code="SEMANTIC_REPAIR_REQUIRED",
                category="VALIDATION",
                retryable=True,
            )
        # Bounded rescue is narrowed to the one targeted field, not the whole contract.
        assert payload["operation"] == "secondary_structured_extraction_partition"
        assert payload["target_fields"] == ["retention"]
        return {"fields": [_item(
            "retention", value="20", unit="years", status="found",
            quote="20-year data retention typical",
        )]}

    monkeypatch.setattr(runtime_bridge, "resolve_semantic_repair_content", lambda _exc: malformed)
    result = _run(monkeypatch, call_json)
    assert len(calls) == 4
    assert result["supplement_rounds"] == 1
    assert result["supplement_status"] == "completed"
    assert result["supplement_target_fields"] == ["retention"]
    assert result["coverage"]["critical_unresolved"] == []
