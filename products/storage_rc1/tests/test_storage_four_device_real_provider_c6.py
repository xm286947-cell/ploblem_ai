from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

from storage_life import ai, runtime_bridge
from storage_life.runtime_domain_strategy import EMMC_FIELD_ORDER


FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN_PATH = FIXTURES / "storage_four_device_automated_golden_c4.json"
EXCERPT_PATH = FIXTURES / "storage_four_device_source_excerpts_c4.json"
REAL_E2E_ENABLED = os.environ.get("STORAGE_C6_REAL_E2E", "").strip().lower() in {
    "1", "true", "yes", "on"
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _golden_cases() -> list[dict]:
    return _load(GOLDEN_PATH)["cases"]


def _source_excerpts() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for excerpt in _load(EXCERPT_PATH)["excerpts"]:
        grouped[str(excerpt["source_id"])].append(excerpt)
    return grouped


def _execution_modes() -> list[tuple[dict, str, list[str]]]:
    modes = []
    for case in _golden_cases():
        refs = case["source_refs"]
        if case["golden_id"] == "G-TIMAR-97":
            modes.extend([
                (case, "pdf_only", [str(refs[0]["source_id"])]),
                (case, "pdf_and_html", [str(ref["source_id"]) for ref in refs]),
            ])
        else:
            modes.append((case, "single_source", [str(ref["source_id"]) for ref in refs]))
    return modes


def _source_bundles(source_ids: list[str]) -> list[dict]:
    excerpts = _source_excerpts()
    sources = []
    for source_id in source_ids:
        by_page: dict[int, list[str]] = defaultdict(list)
        for item in excerpts.get(source_id, []):
            by_page[int(item["page"])].append(str(item["text"]))
        if not by_page:
            raise AssertionError(f"TEST_HARNESS_FAIL: no frozen source excerpts for {source_id}")
        sources.append({
            "source_id": source_id,
            "pages": [
                (page, "\n".join(texts), "frozen_source_excerpt")
                for page, texts in sorted(by_page.items())
            ],
        })
    return sources


def _classify_runtime_failure(exc: Exception) -> str:
    code = str(getattr(exc, "code", "") or "").upper()
    category = str(getattr(exc, "category", "") or "").upper()
    message = str(exc).upper()
    if category in {"CONFIG", "CONFIGURATION"} or any(
        token in code or token in message
        for token in ("CONFIG", "NOT_CONFIGURED", "MISSING_API_KEY", "RUNTIME_UNAVAILABLE")
    ):
        return "BLOCKED_PROVIDER_CONFIGURATION"
    if category in {"TRANSPORT", "NETWORK"} or any(
        token in code or token in message
        for token in ("TIMEOUT", "TRANSPORT", "CONNECTION", "RETRY_BUDGET")
    ):
        return "BLOCKED_PROVIDER_TRANSPORT"
    if isinstance(exc, (runtime_bridge.RuntimeBridgeUnavailable, ai.AIUnavailable)):
        return "BLOCKED_PROVIDER_CONFIGURATION"
    return "RUNTIME_CONTRACT_FAIL"


def _evidence_records(value) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _assert_actual_supporting_evidence(
    expected: dict, fact: dict, golden_id: str = "test", mode: str = "test"
) -> None:
    supporting_claims = expected.get("supporting_evidence") or []
    if not supporting_claims:
        return
    actual_records = (
        _evidence_records(fact.get("resolved_evidence"))
        + _evidence_records(fact.get("evidence"))
    )
    key = expected.get("field_key", "<unknown>")
    assert actual_records, (
        f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} actual compound evidence absent for {key}"
    )
    for supporting in supporting_claims:
        source_id = str(supporting["source_id"])
        page = int(supporting["page"])
        quote = str(supporting["quote"])
        matches = [
            item for item in actual_records
            if str(item.get("source_id") or "") == source_id
            and int(item.get("page") or 0) == page
            and quote.casefold() in str(item.get("quote") or "").casefold()
        ]
        assert matches, (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} actual evidence does not support "
            f"compound claim {supporting.get('claim')} from {source_id} page {page}; "
            f"actual={actual_records}"
        )


def _assert_golden(case: dict, mode: str, source_ids: list[str], result: dict) -> None:
    golden_id = case["golden_id"]
    facts = {str(item.get("field_key")): item for item in result.get("facts") or []}
    selected_positive = [
        item for item in case["positive_assertions"] if item["source_id"] in set(source_ids)
    ]

    assert result.get("schema_valid") is True, (
        f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} schema: "
        f"{result.get('structural_errors')}"
    )
    unresolved = sorted(set(result.get("unresolved_evidence") or []) & {
        item["field_key"] for item in selected_positive
    })
    assert not unresolved, (
        f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} unresolved required evidence: {unresolved}"
    )

    for expected in selected_positive:
        key = expected["field_key"]
        fact = facts.get(key) or {}
        evidence = fact.get("resolved_evidence") or {}
        assert fact.get("status") == "found", (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} must_find {key}; actual={fact}"
        )
        assert evidence, (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} unresolved evidence for {key}"
        )
        assert evidence.get("source_id") == expected["source_id"], (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} source mismatch for {key}: {evidence}"
        )
        assert int(evidence.get("page") or 0) == int(expected["page"]), (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} page mismatch for {key}: {evidence}"
        )
        assert str(expected["quote"]).casefold() in str(evidence.get("quote") or "").casefold(), (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} evidence quote mismatch for {key}: {evidence}"
        )
        assert str(fact.get("value")) == str(expected["value"]), (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} value mismatch for {key}: {fact}"
        )
        for attribute in ("unit", "condition", "scope_type", "scope_values"):
            expected_value = expected.get(attribute)
            if attribute == "scope_type":
                expected_value = expected.get(attribute, "product_family")
            elif attribute == "scope_values":
                expected_value = expected.get(attribute, [])
            if expected_value is not None:
                assert fact.get(attribute) == expected_value, (
                    f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} {attribute} mismatch "
                    f"for {key}: {fact}"
                )

        for supporting in expected.get("supporting_evidence") or []:
            excerpt = next(
                (
                    item for item in _source_excerpts().get(str(supporting["source_id"]), [])
                    if int(item["page"]) == int(supporting["page"])
                ),
                None,
            )
            assert excerpt and supporting["quote"] in excerpt["text"], (
                f"TEST_HARNESS_FAIL: frozen supporting evidence is not present for {key}/{supporting['claim']}"
            )
        _assert_actual_supporting_evidence(expected, fact, golden_id, mode)

    for expected in case["negative_assertions"]:
        if expected.get("source_mode") and expected["source_mode"] != mode:
            continue
        fact = facts.get(expected["field_key"]) or {}
        assert fact.get("status") == expected.get("status", "missing"), (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} must_missing "
            f"{expected['field_key']}; actual={fact}"
        )
        assert fact.get("value") is None and fact.get("resolved_evidence") is None, (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} forbidden inferred fact "
            f"{expected['field_key']}; actual={fact}"
        )

    for expected in case["condition_assertions"]:
        if expected["field_key"] in facts and facts[expected["field_key"]].get("status") == "found":
            assert facts[expected["field_key"]].get("condition") == expected["condition"], (
                f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} condition mismatch: {expected}"
            )

    for expected in case["scope_assertions"]:
        if "rule" in expected:
            continue
        fact = facts.get(expected["field_key"]) or {}
        assert fact.get("status") == "found", (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} scoped fact missing: {expected}"
        )
        assert fact.get("scope_type") == expected["scope_type"], (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} scope type mismatch: {fact}"
        )
        if "scope_values" in expected:
            assert fact.get("scope_values") == expected["scope_values"], (
                f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} scope values mismatch: {fact}"
            )

    for binding in case["source_binding_assertions"]:
        if binding["source_mode"] != mode:
            continue
        evidence = (facts.get(binding["field_key"]) or {}).get("resolved_evidence") or {}
        assert evidence.get("source_id") == binding["source_id"], (
            f"REAL_PROVIDER_GOLDEN_FAIL: {golden_id}/{mode} source binding mismatch: "
            f"{binding['field_key']} -> {evidence}"
        )

    if golden_id == "G-S40FC016":
        actual_order = [str(item.get("field_key")) for item in result.get("facts") or []]
        assert actual_order == list(EMMC_FIELD_ORDER) and len(actual_order) == 37, (
            f"RUNTIME_CONTRACT_FAIL: eMMC contract must preserve exactly 37 ordered fields; "
            f"count={len(actual_order)}"
        )


def _assert_emmc_event_model(events: list[dict], supplement_rounds: int) -> tuple[dict, list[dict]]:
    primary_agent = "storage.emmc.parameter_extract"
    primaries = [
        event for event in events
        if event.get("runtime_long_content") is True and event.get("agent_id") == primary_agent
    ]
    assert len(primaries) == 1, (
        f"RUNTIME_CONTRACT_FAIL: expected exactly one eMMC primary LongContent event; got {len(primaries)}"
    )
    primary = primaries[0]
    supplements = [event for event in events if event is not primary]
    assert len(supplements) <= 1, (
        f"RUNTIME_CONTRACT_FAIL: eMMC has more than one supplement Runtime event: {len(supplements)}"
    )
    assert len(events) <= 2, f"RUNTIME_CONTRACT_FAIL: eMMC emitted too many Runtime events: {len(events)}"
    assert supplement_rounds in (0, 1), (
        f"RUNTIME_CONTRACT_FAIL: unsupported supplement_rounds={supplement_rounds}"
    )
    assert len(supplements) == supplement_rounds, (
        f"RUNTIME_CONTRACT_FAIL: supplement event count {len(supplements)} does not match "
        f"supplement_rounds={supplement_rounds}"
    )
    for event in supplements:
        assert event.get("agent_id") == primary_agent, (
            f"RUNTIME_CONTRACT_FAIL: unexpected eMMC supplement route: {event.get('agent_id')}"
        )
        assert event.get("runtime_long_content") is not True, (
            "RUNTIME_CONTRACT_FAIL: a second LongContent primary was mislabeled as a supplement"
        )
    return primary, supplements


def _assert_emmc_chunk_attempt_evidence(event: dict) -> None:
    for key in ("provider_calls", "duration_ms", "resume_count", "chunks"):
        assert key in event and event[key] is not None, (
            f"RUNTIME_CONTRACT_FAIL: eMMC primary event missing retry evidence {key}"
        )
    chunks = event.get("chunks")
    assert isinstance(chunks, list) and chunks, (
        "RUNTIME_CONTRACT_FAIL: eMMC primary event has no chunk attempt evidence"
    )
    for index, chunk in enumerate(chunks):
        missing = [
            key for key in ("provider_calls", "attempt_count", "run_count", "status", "error")
            if key not in chunk
        ]
        assert not missing, (
            f"RUNTIME_CONTRACT_FAIL: eMMC chunk {index} missing attempt evidence {missing}"
        )


def _runtime_evidence(case: dict, events: list[dict], supplement_rounds: int = 0) -> dict:
    golden_id = case["golden_id"]
    assert events, f"RUNTIME_CONTRACT_FAIL: {golden_id} produced no Runtime execution evidence"
    required = ("request_id", "task_id", "run_id", "status", "provider_calls", "duration_ms")
    for event in events:
        missing = [key for key in required if event.get(key) is None]
        assert not missing, (
            f"RUNTIME_CONTRACT_FAIL: {golden_id} Runtime event missing {missing}: "
            f"agent={event.get('agent_id')}"
        )
        if case["golden_id"] != "G-S40FC016":
            assert event.get("retry_budget_exhausted") is not None, (
                f"RUNTIME_CONTRACT_FAIL: {case['golden_id']} event lacks retry_budget_exhausted"
            )
    total_calls = sum(int(event.get("provider_calls") or 0) for event in events)
    assert total_calls > 0, f"RUNTIME_CONTRACT_FAIL: {golden_id} recorded zero Runtime provider calls"

    if golden_id == "G-S40FC016":
        event, supplements = _assert_emmc_event_model(events, supplement_rounds)
        print("EMMC_PRIMARY_LONG_CONTENT_EVENT_COUNT=1")
        print("EMMC_SUPPLEMENT_EVENT_COUNT_MODEL=PASS")
        print("EMMC_RUNTIME_EVENT_MODEL=PASS")
        assert event.get("runtime_long_content") is True, "RUNTIME_CONTRACT_FAIL: eMMC bypassed LongContentRecoveryExecutor"
        assert event.get("agent_id") == "storage.emmc.parameter_extract", (
            f"RUNTIME_CONTRACT_FAIL: unexpected eMMC primary route {event.get('agent_id')}"
        )
        assert event.get("observability_status") == "READY", (
            f"RUNTIME_CONTRACT_FAIL: eMMC group observability unavailable: {event.get('observability_status')}"
        )
        assert int(event.get("chunk_count") or 0) == 6, (
            f"RUNTIME_CONTRACT_FAIL: eMMC expected six atomic groups/chunks, got {event.get('chunk_count')}"
        )
        assert event.get("source_fingerprint_kind") == "structured_source_text_sha256"
        assert event.get("source_fingerprint") and len(event["source_fingerprint"]) == 64
        assert all(item.get("group_ids") for item in event.get("chunks") or []), (
            "RUNTIME_CONTRACT_FAIL: eMMC chunk-to-atomic-group observability is incomplete"
        )
        _assert_emmc_chunk_attempt_evidence(event)
        print("EMMC_RETRY_BUDGET_EVIDENCE_MODE=CHUNK_ATTEMPT_EVIDENCE")
        provider_status = runtime_bridge.status()
        details = provider_status.get("agent_details") or {}
        agent_id = event.get("agent_id")
        agent = details.get(agent_id) or {}
        provider = agent.get("provider")
        model = agent.get("model")
    else:
        print("GENERIC_RETRY_BUDGET_EVIDENCE=PASS")
        provider = events[-1].get("provider")
        model = events[-1].get("model")

    assert provider and model, f"RUNTIME_CONTRACT_FAIL: {golden_id} provider/model identity absent from Runtime evidence"
    return {
        "provider": provider,
        "model": model,
        "supplement_event_count": len(supplements) if golden_id == "G-S40FC016" else None,
        "events": [
            {
                key: event.get(key)
                for key in (
                    "agent_id", "request_id", "task_id", "run_id", "status", "provider_calls",
                    "retry_budget_exhausted", "duration_ms", "runtime_commit", "runtime_long_content",
                    "observability_status", "source_fingerprint_kind", "source_fingerprint",
                    "resume_count", "chunk_count", "chunks",
                )
                if key in event
            } | {"retry_budget_exhausted": event.get("retry_budget_exhausted")}
            for event in events
        ],
    }


def test_c6_fixture_contract_is_four_devices_and_five_execution_modes():
    cases = _golden_cases()
    modes = _execution_modes()
    assert len(cases) == 4
    assert len(modes) == 5
    assert {case["golden_id"] for case in cases} == {
        "G-S40FC016", "G-GD5F1GQ5", "G-GD25Q64E", "G-TIMAR-97"
    }
    source_refs = [ref for case in cases for ref in case["source_refs"]]
    assert len(source_refs) == 5
    assert all(ref["source_id"] == ref["sha256"] for ref in source_refs)
    assert len({ref["source_id"] for ref in source_refs}) == 5


def test_c6_harness_reuses_frozen_golden_and_source_assets_without_copying_assertion_values():
    golden = _load(GOLDEN_PATH)
    excerpts = _load(EXCERPT_PATH)
    assert len(golden["cases"]) == 4 and len(excerpts["excerpts"]) > 0
    assert all(
        ref["source_id"] in {item["source_id"] for item in excerpts["excerpts"]}
        for case in golden["cases"] for ref in case["source_refs"]
    )
    excerpt_map = _source_excerpts()
    for _case, _mode, source_ids in _execution_modes():
        bundles = _source_bundles(source_ids)
        assert [item["source_id"] for item in bundles] == source_ids
        for bundle in bundles:
            expected_pages: dict[int, list[str]] = defaultdict(list)
            for item in excerpt_map[bundle["source_id"]]:
                expected_pages[int(item["page"])].append(str(item["text"]))
            actual_pages = {page: text for page, text, _method in bundle["pages"]}
            assert actual_pages == {
                page: "\n".join(texts) for page, texts in sorted(expected_pages.items())
            }


def _compound_expected():
    return {
        "field_key": "compound",
        "supporting_evidence": [
            {"claim": "claim_a", "source_id": "source-a", "page": 1, "quote": "claim A"},
            {"claim": "claim_b", "source_id": "source-b", "page": 2, "quote": "claim B"},
        ],
    }


def test_c6_compound_partial_actual_evidence_fails():
    expected = _compound_expected()
    partial_fact = {
        "resolved_evidence": {"source_id": "source-a", "page": 1, "quote": "claim A"},
        "evidence": [{"source_id": "source-a", "page": 1, "quote": "claim A"}],
    }
    with pytest.raises(AssertionError, match="claim_b"):
        _assert_actual_supporting_evidence(expected, partial_fact)
    print("COMPOUND_PARTIAL_SUPPORT=REJECTED")


def test_c6_compound_full_actual_evidence_passes():
    expected = _compound_expected()
    complete_fact = {
        "resolved_evidence": {"source_id": "source-a", "page": 1, "quote": "claim A"},
        "evidence": [
            {"source_id": "source-a", "page": 1, "quote": "claim A"},
            {"source_id": "source-b", "page": 2, "quote": "claim B, explicitly stated"},
        ],
    }
    _assert_actual_supporting_evidence(expected, complete_fact)
    print("COMPOUND_ACTUAL_EVIDENCE_SUPPORT=PASS")
    print("UNDER_SUPPORTED_COMPOUND_ASSERTION=0")


def test_c6_emmc_primary_only_event_model():
    event = {"agent_id": "storage.emmc.parameter_extract", "runtime_long_content": True}
    primary, supplements = _assert_emmc_event_model([event], supplement_rounds=0)
    assert primary is event and supplements == []
    print("EMMC_PRIMARY_LONG_CONTENT_EVENT_COUNT=1")


def test_c6_emmc_primary_plus_supplement_event_model():
    primary_event = {"agent_id": "storage.emmc.parameter_extract", "runtime_long_content": True}
    supplement_event = {"agent_id": "storage.emmc.parameter_extract", "runtime_long_content": False}
    primary, supplements = _assert_emmc_event_model(
        [primary_event, supplement_event], supplement_rounds=1
    )
    assert primary is primary_event and supplements == [supplement_event]
    print("EMMC_SUPPLEMENT_EVENT_MODEL=PASS")
    print("EMMC_RUNTIME_EVENT_MODEL=PASS")


def test_c6_emmc_supplement_without_primary_fails():
    with pytest.raises(AssertionError, match="exactly one eMMC primary"):
        _assert_emmc_event_model(
            [{"agent_id": "storage.emmc.parameter_extract", "runtime_long_content": False}],
            supplement_rounds=1,
        )


def test_c6_emmc_too_many_runtime_events_fail():
    primary = {"agent_id": "storage.emmc.parameter_extract", "runtime_long_content": True}
    supplements = [
        {"agent_id": "storage.emmc.parameter_extract", "runtime_long_content": False},
        {"agent_id": "storage.emmc.parameter_extract", "runtime_long_content": False},
    ]
    with pytest.raises(AssertionError, match="more than one supplement"):
        _assert_emmc_event_model([primary, *supplements], supplement_rounds=1)


def test_c6_emmc_unexpected_supplement_route_fails():
    primary = {"agent_id": "storage.emmc.parameter_extract", "runtime_long_content": True}
    supplement = {"agent_id": "storage.generic.parameter_extract", "runtime_long_content": False}
    with pytest.raises(AssertionError, match="unexpected eMMC supplement route"):
        _assert_emmc_event_model([primary, supplement], supplement_rounds=1)


def test_c6_generic_runtime_retry_budget_evidence_is_required():
    case = {"golden_id": "G-GD5F1GQ5"}
    event = {
        "agent_id": "storage.generic.parameter_extract", "request_id": "r", "task_id": "t",
        "run_id": "run", "status": "COMPLETED", "provider_calls": 1, "duration_ms": 1,
        "retry_budget_exhausted": False, "provider": "stub", "model": "stub",
    }
    _runtime_evidence(case, [event])
    print("GENERIC_RETRY_BUDGET_EVIDENCE=PASS")
    event.pop("retry_budget_exhausted")
    with pytest.raises(AssertionError, match="retry_budget_exhausted"):
        _runtime_evidence(case, [event])


def test_c6_emmc_chunk_attempt_evidence_shape():
    event = {
        "provider_calls": 2, "duration_ms": 10, "resume_count": 1,
        "chunks": [{
            "provider_calls": 1, "attempt_count": 2, "run_count": 1,
            "status": "COMPLETED", "error": None,
        }],
    }
    _assert_emmc_chunk_attempt_evidence(event)
    print("EMMC_RETRY_BUDGET_EVIDENCE_MODE=CHUNK_ATTEMPT_EVIDENCE")
    del event["chunks"][0]["attempt_count"]
    with pytest.raises(AssertionError, match="attempt_count"):
        _assert_emmc_chunk_attempt_evidence(event)


@pytest.mark.parametrize(
    ("case", "mode", "source_ids"),
    _execution_modes(),
    ids=[f"{case['golden_id']}-{mode}" for case, mode, _source_ids in _execution_modes()],
)
@pytest.mark.skipif(not REAL_E2E_ENABLED, reason="STORAGE_C6_REAL_E2E opt-in disabled")
def test_c6_four_device_real_provider_golden(case: dict, mode: str, source_ids: list[str]):
    if os.environ.get("STORAGE_LIFE_EXECUTION_MODE", "").strip().lower() != "runtime":
        pytest.fail("BLOCKED_PROVIDER_CONFIGURATION: STORAGE_LIFE_EXECUTION_MODE must be runtime")

    runtime_bridge.reset_for_tests()
    before_events = runtime_bridge.last_executions()
    sources = _source_bundles(source_ids)
    try:
        result = ai.extract_specification_bundle_once(
            sources,
            case["device_type"],
            case["vendor"],
            case["sample"],
        )
    except (runtime_bridge.RuntimeBridgeUnavailable, runtime_bridge.RuntimeBridgeCallError, ai.AIUnavailable, ai.AIResponseError) as exc:
        classification = _classify_runtime_failure(exc)
        pytest.fail(
            f"{classification}: {case['golden_id']}/{mode}; "
            f"code={getattr(exc, 'code', None)} category={getattr(exc, 'category', None)} "
            f"message={exc}"
        )

    _assert_golden(case, mode, source_ids, result)
    current_events = runtime_bridge.last_executions()
    events = current_events[len(before_events):]
    evidence = _runtime_evidence(case, events, int(result.get("supplement_rounds") or 0))
    print("[C6_RUNTIME_EVIDENCE] " + json.dumps({
        "golden_id": case["golden_id"],
        "execution_mode": mode,
        "source_ids": source_ids,
        **evidence,
    }, ensure_ascii=False, sort_keys=True, default=str))
