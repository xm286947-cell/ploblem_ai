from __future__ import annotations

import pytest

from storage_life.engineering_decision import (
    FIT,
    NOT_FIT,
    UNKNOWN,
    compose_role_views,
    derive_required_storage_profile,
    match_candidate,
)


def test_nand_required_pe_budget_uses_explicit_stress_only():
    profile = derive_required_storage_profile(
        {
            "target_service_life_years": 10,
            "operating_days_per_year": 365,
            "design_margin_ratio": 0.25,
            "required_retention_years": 10,
        },
        {"pe_cycles_per_day": 2},
    )
    assert profile["status"] == "READY"
    assert profile["required_pe_cycles"] == pytest.approx(9125)
    assert profile["decision_boundary"] == "NO_INFERRED_WAF_OR_WEAR_MODEL"
    assert profile["derivation_trace"][0]["formula"].startswith("pe_cycles_per_day")


def test_nand_required_profile_fails_closed_without_pe_stress():
    profile = derive_required_storage_profile(
        {"target_service_life_years": 10},
        {"logical_write_bytes_per_day": 1024},
    )
    assert profile["status"] == "PARTIAL"
    assert profile["required_pe_cycles"] is None
    assert "PE_CYCLES_PER_DAY_REQUIRED_FOR_NAND_PE_BUDGET" in profile["missing_information"]


def test_procurement_match_uses_confirmed_fact_values_and_evidence():
    required = {
        "required_pe_cycles": 50_000,
        "required_retention_years": 10,
        "minimum_ecc_correctable_bits": 4,
        "ecc_step_bytes": 528,
        "max_operating_temperature_c": 70,
    }
    facts = [
        {"canonical_name": "pe_cycles", "value": "100000", "unit": "cycles", "evidence": [{"evidence_id": "E-PE"}]},
        {"canonical_name": "data_retention", "value": "10", "unit": "Years", "evidence": [{"evidence_id": "E-RET"}]},
        {"canonical_name": "ecc_capability", "value": "4 bits / 528 Byte", "unit": "", "evidence": [{"evidence_id": "E-ECC"}]},
        {"canonical_name": "operating_temperature", "value": "-40 ~ 85", "unit": "C", "evidence": [{"evidence_id": "E-TEMP"}]},
    ]
    result = match_candidate(required, facts, candidate_id="nand-a")
    assert result["status"] == FIT
    assert result["automatic_purchase_approval"] is False
    assert all(x["evidence"] for x in result["checks"])


def test_procurement_match_not_fit_when_pe_endurance_is_too_low():
    result = match_candidate(
        {"required_pe_cycles": 120_000},
        [{"canonical_name": "pe_cycles", "value": "100000", "unit": "cycles", "evidence": [{"evidence_id": "E"}]}],
        candidate_id="nand-a",
    )
    assert result["status"] == NOT_FIT
    assert result["checks"][0]["status"] == "FAIL"


def test_procurement_unknown_never_becomes_safe_when_required_fact_missing():
    result = match_candidate(
        {"required_pe_cycles": 50_000, "required_retention_years": 10},
        [{"canonical_name": "pe_cycles", "value": "100000", "unit": "cycles", "evidence": [{"evidence_id": "E"}]}],
        candidate_id="nand-a",
    )
    assert result["status"] == UNKNOWN
    assert "CANDIDATE_RETENTION_UNKNOWN_OR_UNIT_UNSUPPORTED" in result["unknowns"]
    assert result["automatic_purchase_approval"] is False


def test_role_view_requires_formal_nand_knowledge_with_evidence():
    profile = derive_required_storage_profile(
        {"target_service_life_years": 10},
        {"pe_cycles_per_day": 1},
    )
    result = compose_role_views(
        device={"id": "d1", "device_type": "NAND Flash"},
        required_profile=profile,
        hardware_facts=[],
        formal_nand_knowledge={"results": []},
    )
    assert result["overall_status"] == "PARTIAL_FAIL_CLOSED"
    assert "FORMAL_NAND_KNOWLEDGE_WITH_EVIDENCE_REQUIRED" in result["blockers"]
    assert result["rules"]["unknown_is_safe"] is False


def test_role_view_can_be_ready_when_profile_and_formal_knowledge_are_ready():
    profile = derive_required_storage_profile(
        {"target_service_life_years": 10},
        {"pe_cycles_per_day": 1},
    )
    result = compose_role_views(
        device={"id": "d1", "device_type": "NAND Flash"},
        required_profile=profile,
        hardware_facts=[{"canonical_name": "pe_cycles", "value": 100000}],
        formal_nand_knowledge={
            "results": [{"object_id": "KO-NAND-PE", "evidence_refs": ["EVD-NAND-PE"]}]
        },
    )
    assert result["overall_status"] == "READY_FOR_DEMO"
    assert result["formal_knowledge"]["knowledge_refs"] == ["KO-NAND-PE"]
    assert result["formal_knowledge"]["evidence_refs"] == ["EVD-NAND-PE"]
