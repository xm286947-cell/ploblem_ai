from storage_life.runtime_domain_strategy import (
    EMMC_ATOMIC_GROUPS,
    EMMC_FIELD_ORDER,
    StorageEmmcDomainStrategy,
)


def _source_ref():
    return {
        "source_id": "skyhigh_s40fc016_002_01118_rev_d",
        "source_type": "datasheet_markdown",
        "revision": "D",
        "content_hash": "md-hash",
        "fingerprint": "fp-001",
        "metadata": {"raw_pdf_sha256": "pdf-hash"},
    }


def _fact(key, value="x", status="found", evidence=True):
    return {
        "field_key": key,
        "value": value if status != "missing" else None,
        "unit": None,
        "condition": None,
        "scope_type": "product_family",
        "scope_values": [],
        "evidence": ([{"source_id": _source_ref()["source_id"], "page": 1, "quote": key}] if evidence else []),
        "confidence": 0.9,
        "status": status,
        "derived": False,
        "knowledge_type": "specification",
    }


def test_emmc_field_universe_is_exactly_37_and_unique():
    assert len(EMMC_FIELD_ORDER) == 37
    assert len(set(EMMC_FIELD_ORDER)) == 37


def test_atomic_groups_cover_every_field_once_and_fit_default_group_limit():
    flat = [field for _, fields in EMMC_ATOMIC_GROUPS for field in fields]
    assert set(flat) == set(EMMC_FIELD_ORDER)
    assert len(flat) == len(set(flat)) == 37
    assert max(len(fields) for _, fields in EMMC_ATOMIC_GROUPS) <= 8


def test_descriptor_projects_37_logical_units_and_six_keep_together_groups():
    descriptor = StorageEmmcDomainStrategy.descriptor(
        source_ref=_source_ref(),
        source_text="# Page 1\nSkyHigh S40FC016",
        partition_key="storage:emmc:fp-001",
    )
    assert descriptor["strategy_ref"] == "storage_emmc_field_groups@1"
    assert len(descriptor["logical_units"]) == 37
    assert len(descriptor["atomic_groups"]) == 6
    assert all(group["policy"] == "KEEP_TOGETHER" for group in descriptor["atomic_groups"])
    assert len(descriptor["coverage_universe"]["required_units"]) == 37
    assert descriptor["coverage_universe"]["coverage_type"] == "ITEM"


def test_descriptor_source_fingerprint_changes_coverage_universe_identity():
    a = StorageEmmcDomainStrategy.descriptor(source_ref=_source_ref(), source_text="A")
    b_ref = _source_ref()
    b_ref["fingerprint"] = "fp-002"
    b = StorageEmmcDomainStrategy.descriptor(source_ref=b_ref, source_text="A")
    assert a["coverage_universe"]["universe_fingerprint"] != b["coverage_universe"]["universe_fingerprint"]


def test_business_merger_orders_fields_and_deduplicates_equivalent_evidence():
    partials = [
        {"fields": [_fact("revision", "D"), _fact("manufacturer", "SkyHigh Memory")]},
        {"fields": [_fact("manufacturer", "SkyHigh Memory")]},
    ]
    result = StorageEmmcDomainStrategy.merge_partials(partials)
    assert [x["field_key"] for x in result["fields"]] == ["manufacturer", "revision"]
    assert result["merge_conflicts"] == []
    assert result["missing_field_keys"]


def test_business_merger_never_silently_prefers_conflicting_duplicate():
    result = StorageEmmcDomainStrategy.merge_partials(
        [
            {"fields": [_fact("native_nand_type", "MLC")]},
            {"fields": [_fact("native_nand_type", "SLC")]},
        ]
    )
    field = result["fields"][0]
    assert field["field_key"] == "native_nand_type"
    assert field["status"] == "conflict"
    assert result["merge_conflicts"][0]["type"] == "semantic_conflict"


def test_merger_does_not_fabricate_missing_facts_for_unprocessed_coverage():
    result = StorageEmmcDomainStrategy.merge_partials([{"fields": [_fact("manufacturer", "SkyHigh Memory")]}])
    assert result["field_count"] == 1
    assert len(result["missing_field_keys"]) == 36
    assert not result["complete"]
    assert not any(item["field_key"] == "pe_cycle" for item in result["fields"])


def test_business_gate_accepts_legitimate_missing_status_when_37_fields_present():
    facts = []
    legitimate_missing = {
        "pe_cycle",
        "data_retention",
        "endurance_condition",
        "erase_cycle_granularity",
        "error_reporting",
    }
    for key in EMMC_FIELD_ORDER:
        if key in legitimate_missing:
            facts.append(_fact(key, status="missing", evidence=False))
        else:
            facts.append(_fact(key))
    merged = StorageEmmcDomainStrategy.merge_partials([{"fields": facts}])
    gate = StorageEmmcDomainStrategy.business_gate(merged, evidence_resolved=True, review_resolved=True)
    assert merged["field_count"] == 37
    assert merged["missing_field_keys"] == []
    assert gate["passed"] is True
    assert gate["business_consumable"] is True


def test_business_gate_blocks_conflict_until_review_resolved():
    facts = [_fact(key) for key in EMMC_FIELD_ORDER]
    for item in facts:
        if item["field_key"] == "revision":
            item["status"] = "conflict"
    merged = StorageEmmcDomainStrategy.merge_partials([{"fields": facts}])
    blocked = StorageEmmcDomainStrategy.business_gate(merged, review_resolved=False)
    accepted = StorageEmmcDomainStrategy.business_gate(merged, review_resolved=True)
    assert blocked["passed"] is False
    assert "BUSINESS_REVIEW_REQUIRED" in blocked["reasons"]
    assert accepted["passed"] is True


def test_business_gate_blocks_found_fact_without_evidence():
    facts = [_fact(key) for key in EMMC_FIELD_ORDER]
    facts[0]["evidence"] = []
    merged = StorageEmmcDomainStrategy.merge_partials([{"fields": facts}])
    gate = StorageEmmcDomainStrategy.business_gate(merged)
    assert gate["passed"] is False
    assert "EVIDENCE_NOT_RESOLVED" in gate["reasons"]
