from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from storage_life import ai, parameter_baseline, runtime_bridge


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "storage_359_real_source_excerpts.json"


def _enabled() -> bool:
    return os.environ.get("STORAGE_359_REAL_E2E", "").strip().lower() in {"1", "true", "yes", "on"}


def _load_cases() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["sources"]


def _fact_text(fact: dict) -> str:
    evidence = fact.get("resolved_evidence") or {}
    parts = [
        fact.get("value"),
        fact.get("unit"),
        fact.get("condition"),
        " ".join(str(x) for x in fact.get("scope_values") or []),
        evidence.get("quote"),
    ]
    return " ".join(str(x) for x in parts if x is not None).casefold()


def _run_case(case: dict) -> dict:
    pages = [
        (int(item["page"]), str(item["text"]), str(item.get("method") or "real_source_excerpt"))
        for item in case["pages"]
    ]
    return ai.extract_specification_once(
        pages,
        case["device_type"],
        case["vendor"],
        case["product_family"],
        source_id=case["source_id"],
    )


def _assert_golden(case_name: str, case: dict, result: dict) -> None:
    facts = {str(x.get("field_key")): x for x in result.get("facts") or []}
    golden = case["golden"]

    false_negative = []
    for key in golden.get("must_find") or []:
        fact = facts.get(key) or {}
        if fact.get("status") != "found" or not fact.get("resolved_evidence"):
            false_negative.append(key)
    assert false_negative == [], f"{case_name} direct-fact false negatives: {false_negative}"

    false_positive = []
    for key in golden.get("must_not_find") or []:
        fact = facts.get(key) or {}
        if fact.get("status") == "found":
            false_positive.append({key: fact.get("value")})
    assert false_positive == [], f"{case_name} direct-fact false positives: {false_positive}"

    for key, required_terms in (golden.get("required_terms") or {}).items():
        text = _fact_text(facts[key])
        missing_terms = [term for term in required_terms if str(term).casefold() not in text]
        assert not missing_terms, f"{case_name} {key} missing evidence/value terms: {missing_terms}; actual={text}"

    for key, required_terms in (golden.get("required_condition_terms") or {}).items():
        fact = facts[key]
        text = " ".join(
            str(x)
            for x in (
                fact.get("condition"),
                (fact.get("resolved_evidence") or {}).get("quote"),
            )
            if x is not None
        ).casefold()
        missing_terms = [term for term in required_terms if str(term).casefold() not in text]
        assert not missing_terms, f"{case_name} {key} lost condition terms: {missing_terms}; actual={text}"

    assert result.get("schema_valid") is True, result.get("structural_errors")
    unresolved_must_find = sorted(set(result.get("unresolved_evidence") or []) & set(golden.get("must_find") or []))
    assert unresolved_must_find == [], f"{case_name} unresolved Golden evidence: {unresolved_must_find}"

    print(
        "[REAL_GOLDEN]",
        case_name,
        "FALSE_NEGATIVE=0",
        "FALSE_POSITIVE=0",
        "MODEL_CALLS=" + str(result.get("model_calls")),
    )


@pytest.mark.skipif(not _enabled(), reason="STORAGE_359_REAL_E2E opt-in disabled")
def test_359_real_provider_timar_97_direct_facts():
    runtime_bridge.reset_for_tests()
    case = _load_cases()["timar_97"]
    result = _run_case(case)
    _assert_golden("G-TIMAR-97", case, result)


@pytest.mark.skipif(not _enabled(), reason="STORAGE_359_REAL_E2E opt-in disabled")
def test_359_real_provider_gd5f_direct_facts_and_observability_boundary():
    runtime_bridge.reset_for_tests()
    case = _load_cases()["gd5f1gq5"]
    result = _run_case(case)
    _assert_golden("G-GD5F1GQ5", case, result)

    # The real source contains bad-block-management text, but the product baseline must
    # not convert that descriptive fact into a bad-block-count observability claim.
    product_fields = {
        x["canonical_name"]: x
        for x in parameter_baseline.product_fields("NAND Flash", ai.expected_fields("NAND Flash"))
    }
    assert "runtime_bad_block" not in product_fields["bad_block_observability"]["aliases"]

    facts = {str(x.get("field_key")): x for x in result.get("facts") or []}
    assert facts["program_fail"]["status"] == "found"
    assert facts["erase_fail"]["status"] == "found"
    assert facts["ecc_status"]["status"] == "found"
