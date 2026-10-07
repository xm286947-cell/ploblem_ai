from __future__ import annotations

import json
import hashlib

import pytest

from storage_life.nand_lifetime_case import (
    ControlledLifetimeScenario,
    NandLifetimeCaseRequest,
    NandLifetimeRoleRequest,
    build_gd5f1gq5_lifetime_case,
    build_nand_lifetime_role_consumption,
)
import storage_life.nand_lifetime_case as case_module


SOURCE_ID = "src_72f434b245f91cdfea9b7a8b"
REVISION = "rev_99e7833a64c2bf9b85272218"


def _bound_services(*, mismatch_revision: str | None = None):
    texts = {
        "pe": "- P/E cycles with ECC: 100K",
        "retention": "- Data retention: 10 Years",
        "capacity": "◆ 1Gb SLC NAND Flash",
    }
    queries = {
        "GD5F1GQ5 P/E cycles with ECC 100K": ("pe", {"line": 21, "page": 4, "section": "FEATURE", "type": "pdf_text"}),
        "GD5F1GQ5 Data retention 10 Years": ("retention", {"line": 22, "page": 4, "section": "FEATURE", "type": "pdf_text"}),
        "GD5F1GQ5 1Gb SLC NAND Flash": ("capacity", {"line": 5, "page": 4, "section": "FEATURE", "type": "pdf_text"}),
    }
    ids = {key: f"citation-{key}" for key in texts}

    def formal_query():
        return {"status": "NO_MATCH", "items": [], "knowledge_refs": [], "evidence_refs": []}

    def search(query: str):
        if query not in queries:
            return {"hits": []}
        key, locator = queries[query]
        return {"hits": [{
            "hit_id": ids[key],
            "source_id": SOURCE_ID,
            "source_revision": REVISION,
            "locator": json.dumps(locator),
            "text": texts[key],
            "score": 1.0,
        }]}

    def citation_lookup(citation_id: str):
        key = next(name for name, value in ids.items() if value == citation_id)
        locator = queries[next(q for q, pair in queries.items() if pair[0] == key)][1]
        return {
            "citation_id": citation_id,
            "source_id": SOURCE_ID,
            "source_revision": mismatch_revision or REVISION,
            "locator": json.dumps(locator),
            "text": texts[key],
            "content_sha256": "content-hash",
            "original_filename": "STDL-PM-009_gd5f1gq5.pdf",
        }

    def source_lookup(source_id: str):
        return {
            "source": {
                "source_id": source_id,
                "title": "STDL-PM-009_gd5f1gq5.pdf",
                "publisher": "GigaDevice Semiconductor Inc.",
                "source_class": "PUBLIC",
            },
            "revisions": [{"revision_id": REVISION}],
        }

    return formal_query, search, citation_lookup, source_lookup


def test_case_uses_exact_citations_and_fails_closed_without_workload_inputs():
    dependencies = _bound_services()
    result = build_gd5f1gq5_lifetime_case(
        NandLifetimeCaseRequest(),
        formal_query_fn=dependencies[0],
        search_fn=dependencies[1],
        citation_lookup=dependencies[2],
        source_lookup=dependencies[3],
    )

    facts = {fact["fact_id"]: fact for fact in result["A_KNOWN_FACTS"]}
    assert facts["F-NAND-PE-ECC"]["value"] == 100_000
    assert facts["F-NAND-PE-ECC"]["unit"] == "P/E cycles"
    assert facts["F-NAND-RETENTION"]["value"] == 10
    assert facts["F-NAND-NOMINAL-DENSITY"]["value"] == {
        "magnitude": 1.0,
        "unit": "Gb",
        "cell_type": "SLC",
    }
    assert all(fact["claim_binding"].startswith("PASS_") for fact in facts.values())
    assert result["source_precedence"]["formal_nand_coverage"] == "NO_MATCHING_PUBLISHED_NAND_KNOWLEDGE"
    assert result["source_precedence"]["public_knowledge_role"] == "SUPPLEMENT_ONLY"
    assert result["D_DERIVATION"]["status"] == "NOT_RUN"
    assert result["D_DERIVATION"]["calculation"] is None
    assert result["C_ASSUMPTIONS"] == []
    assert result["E_DECISION"] == "INSUFFICIENT_EVIDENCE"
    assert result["R4_CONTROLLED_SCENARIO_CALCULATION"] is None
    assert result["state_mutation"] == "NO"
    assert result["provider_calls"] == 0
    assert result["F_ACTION_PLAN"]
    assert all(item.get("owner") and item.get("verification") and item.get("next_stage") for item in result["F_ACTION_PLAN"])


def test_case_rejects_citation_revision_mismatch_as_source_fact():
    dependencies = _bound_services(mismatch_revision="rev-not-the-search-revision")
    result = build_gd5f1gq5_lifetime_case(
        NandLifetimeCaseRequest(),
        formal_query_fn=dependencies[0],
        search_fn=dependencies[1],
        citation_lookup=dependencies[2],
        source_lookup=dependencies[3],
    )
    assert result["A_KNOWN_FACTS"] == []
    assert result["E_DECISION"] == "INSUFFICIENT_EVIDENCE"
    gap = next(item for item in result["B_MISSING_FACTS"] if item["name"] == "unretrieved_or_unbound_device_source_facts")
    assert gap["status"] == "MISSING_EVIDENCE_BINDING"


def test_case_rejects_wrong_source_and_unsupported_device():
    formal, search, citation, source = _bound_services()

    def wrong_source(_source_id: str):
        value = source(SOURCE_ID)
        value["source"]["title"] = "unrelated.pdf"
        return value

    result = build_gd5f1gq5_lifetime_case(
        NandLifetimeCaseRequest(),
        formal_query_fn=formal,
        search_fn=search,
        citation_lookup=citation,
        source_lookup=wrong_source,
    )
    assert result["A_KNOWN_FACTS"] == []

    with pytest.raises(ValueError, match="UNSUPPORTED_ENGINEERING_CASE_DEVICE"):
        build_gd5f1gq5_lifetime_case(
            NandLifetimeCaseRequest(device_model="OTHER-NAND"),
            formal_query_fn=formal,
            search_fn=search,
            citation_lookup=citation,
            source_lookup=source,
        )


def test_case_uses_hash_verified_source_snapshot_when_gateway_search_snippets_are_irrelevant(monkeypatch):
    raw = b"immutable-source-pdf-snapshot"
    raw_sha = hashlib.sha256(raw).hexdigest()

    class Page:
        def __init__(self, text=""):
            self.text = text

        def extract_text(self):
            return self.text

    class Reader:
        def __init__(self, _stream, strict=True):
            self.pages = [Page(), Page(), Page(), Page(
                "Feature\n1Gb SLC NAND Flash\nP/E cycles with ECC: 100K\nData retention: 10 Years"
            )]

    monkeypatch.setattr(case_module, "PdfReader", Reader)
    queries = {
        "GD5F1GQ5 SPI-NAND datasheet": [{
            "hit_id": "identity-hit",
            "source_id": SOURCE_ID,
            "source_revision": REVISION,
            "text": "SPI-NAND GD5F1GQ5",
        }],
        "GD5F1GQ5 P/E cycles with ECC 100K": [{"hit_id": "irrelevant-pe", "source_id": SOURCE_ID, "source_revision": REVISION, "text": "SPI-NAND GD5F1GQ5"}],
        "GD5F1GQ5 Data retention 10 Years": [{"hit_id": "irrelevant-retention", "source_id": SOURCE_ID, "source_revision": REVISION, "text": "SPI-NAND GD5F1GQ5"}],
        "GD5F1GQ5 1Gb SLC NAND Flash": [{"hit_id": "irrelevant-capacity", "source_id": SOURCE_ID, "source_revision": REVISION, "text": "SPI-NAND GD5F1GQ5"}],
    }

    def search(query):
        return {"hits": queries.get(query, [])}

    def citation_lookup(citation_id):
        return {"citation_id": citation_id}

    def source_lookup(source_id):
        return {
            "source": {
                "source_id": source_id,
                "title": "STDL-PM-009_gd5f1gq5.pdf",
                "publisher": "GigaDevice Semiconductor Inc.",
                "source_class": "PUBLIC",
            },
            "revisions": [{"revision_id": REVISION, "raw_sha256": raw_sha, "content_sha256": "content-hash", "original_filename": "STDL-PM-009_gd5f1gq5.pdf"}],
        }

    result = build_gd5f1gq5_lifetime_case(
        NandLifetimeCaseRequest(),
        formal_query_fn=lambda: {"status": "NO_MATCH", "items": [], "knowledge_refs": [], "evidence_refs": []},
        search_fn=search,
        citation_lookup=citation_lookup,
        source_lookup=source_lookup,
        snapshot_lookup=lambda source_id, revision_id: (raw, {"content_type": "application/pdf", "content_length": "1"}),
    )
    facts = {item["fact_id"]: item for item in result["A_KNOWN_FACTS"]}
    assert facts["F-NAND-PE-ECC"]["value"] == 100_000
    assert facts["F-NAND-PE-ECC"]["evidence"]["evidence_type"] == "PUBLIC_KNOWLEDGE_SOURCE_REVISION_SNAPSHOT"
    assert facts["F-NAND-PE-ECC"]["evidence"]["locator"]["page"] == 4
    assert "P/E cycles with ECC: 100K" in facts["F-NAND-PE-ECC"]["evidence"]["quote"]
    assert result["source_snapshot_identity"]["snapshot_sha256"] == raw_sha


def test_r4_screen_is_reproducible_and_never_qualifies_real_device():
    dependencies = _bound_services()
    scenario = ControlledLifetimeScenario(
        scenario_id="R4-GD5-CONTROLLED-PASS",
        host_write_gib_per_day=0.5,
        waf=3.0,
        wear_pool_fraction_of_nominal=0.8,
        wear_distribution_factor=1.5,
        required_pe_margin_fraction=0.2,
    )
    request = NandLifetimeCaseRequest(target_service_life_years=5, controlled_scenario=scenario)
    kwargs = dict(
        formal_query_fn=dependencies[0],
        search_fn=dependencies[1],
        citation_lookup=dependencies[2],
        source_lookup=dependencies[3],
    )
    result = build_gd5f1gq5_lifetime_case(request, **kwargs)
    repeated = build_gd5f1gq5_lifetime_case(request, **kwargs)
    calculation = result["R4_CONTROLLED_SCENARIO_CALCULATION"]
    assert calculation["formula_id"] == "ILLUSTRATIVE_UNIFORM_WEAR_SCREEN_V1"
    assert calculation["formula_approval"] == "NOT_APPROVED_FOR_DEVICE_QUALIFICATION"
    assert calculation["scenario_result"] == "PASS"
    assert calculation["device_decision"] == "INSUFFICIENT_EVIDENCE"
    assert result["E_DECISION"] == "INSUFFICIENT_EVIDENCE"
    assert float(calculation["formula"]["host_write_bytes"]) == pytest.approx(979789414400)
    assert float(calculation["formula"]["nand_media_write_bytes"]) == pytest.approx(2939368243200)
    assert float(calculation["formula"]["projected_max_pe_cycles"]) == pytest.approx(44090.523648)
    assert calculation["formula"] == repeated["R4_CONTROLLED_SCENARIO_CALCULATION"]["formula"]
    kinds = {row["kind"] for row in calculation["input_trace"]}
    assert {"SOURCE_FACT", "BUSINESS_INPUT", "ENGINEERING_SCENARIO_INPUT", "ENGINEERING_ASSUMPTION"} <= kinds
    pe_input = next(row for row in calculation["input_trace"] if row["name"] == "额定P/E cycles with ECC")
    assert pe_input["qualification_eligible"] is False
    assert calculation["validation_plan"]
    assert result["provider_calls"] == 0
    assert result["state_mutation"] == "NO"


@pytest.mark.parametrize(("daily_gib", "expected"), [(1.1, "MARGIN_GAP"), (1.5, "FAIL")])
def test_r4_screen_applies_margin_gap_and_fail_boundaries(daily_gib, expected):
    dependencies = _bound_services()
    scenario = ControlledLifetimeScenario(
        scenario_id=f"R4-GD5-BOUNDARY-{expected}",
        host_write_gib_per_day=daily_gib,
        waf=3.0,
        wear_pool_fraction_of_nominal=0.8,
        wear_distribution_factor=1.5,
        required_pe_margin_fraction=0.2,
    )
    result = build_gd5f1gq5_lifetime_case(
        NandLifetimeCaseRequest(target_service_life_years=5, controlled_scenario=scenario),
        formal_query_fn=dependencies[0],
        search_fn=dependencies[1],
        citation_lookup=dependencies[2],
        source_lookup=dependencies[3],
    )
    calculation = result["R4_CONTROLLED_SCENARIO_CALCULATION"]
    assert calculation["scenario_result"] == expected
    assert calculation["device_decision"] == "INSUFFICIENT_EVIDENCE"


def test_r4_screen_fails_closed_when_source_facts_are_unavailable():
    scenario = ControlledLifetimeScenario(
        scenario_id="R4-GD5-MISSING-SOURCE-FACTS",
        host_write_gib_per_day=0.5,
        waf=3.0,
        wear_pool_fraction_of_nominal=0.8,
        wear_distribution_factor=1.5,
        required_pe_margin_fraction=0.2,
    )
    result = build_gd5f1gq5_lifetime_case(
        NandLifetimeCaseRequest(target_service_life_years=5, controlled_scenario=scenario),
        formal_query_fn=lambda: {"status": "NO_MATCH", "items": [], "knowledge_refs": [], "evidence_refs": []},
        search_fn=lambda _query: {"hits": []},
        citation_lookup=lambda _citation_id: {},
        source_lookup=lambda _source_id: {},
    )
    calculation = result["R4_CONTROLLED_SCENARIO_CALCULATION"]
    assert calculation["status"] == "NOT_RUN"
    assert calculation["scenario_result"] == "INSUFFICIENT_EVIDENCE"
    assert calculation["device_decision"] == "INSUFFICIENT_EVIDENCE"
    assert result["E_DECISION"] == "INSUFFICIENT_EVIDENCE"


def test_r5_six_roles_consume_one_shared_case_without_model_or_retrieval_replay():
    dependencies = _bound_services()
    counters = {"formal": 0, "search": 0}

    def formal_query():
        counters["formal"] += 1
        return dependencies[0]()

    def search(query):
        counters["search"] += 1
        return dependencies[1](query)

    result = build_nand_lifetime_role_consumption(
        NandLifetimeRoleRequest(device_model="GD5F1GQ5", target_service_life_years=5),
        formal_query_fn=formal_query,
        search_fn=search,
        citation_lookup=dependencies[2],
        source_lookup=dependencies[3],
    )
    shared = result["shared_case"]
    roles = result["role_views"]
    assert len(roles) == 6
    assert counters == {"formal": 1, "search": 4}
    assert result["consistency"]["shared_fact_consistency"] == "PASS"
    assert result["consistency"]["shared_evidence_consistency"] == "PASS"
    assert result["consistency"]["shared_derivation_consistency"] == "PASS"
    assert result["consistency"]["cross_role_contradiction_count"] == 0
    assert result["consistency"]["screening_not_promoted_to_device_qualification"] is True
    fact_ids = [fact["fact_id"] for fact in shared["A_KNOWN_FACTS"]]
    fingerprint = result["shared_case_fingerprint"]
    for role in roles.values():
        assert role["shared_case_fingerprint"] == fingerprint
        assert role["shared_fact_refs"] == fact_ids
        assert role["device_decision"] == "INSUFFICIENT_EVIDENCE"
        assert role["status"] == "ACTIONABLE_WITH_OPEN_GATE"
        assert role["next_actions"]
        assert "A_KNOWN_FACTS" not in role
        assert role["model_calls"] == 0
    assert [item["scenario_result"] for item in result["scenario_results"]] == ["PASS", "MARGIN_GAP", "FAIL"]
    assert all(item["device_decision"] == "INSUFFICIENT_EVIDENCE" for item in result["scenario_results"])
    assert counters["formal"] == 1
