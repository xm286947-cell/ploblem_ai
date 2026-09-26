from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from contracts.common_evidence import (
    COMMON_EVIDENCE_CONTRACT_VERSION,
    CommonEvidence,
    map_business_object_evidence,
    map_external_source_evidence,
    map_hardware_case_evidence,
    map_historical_case_evidence,
    map_major_issue_evidence,
    map_quality_scenario_evidence,
)


def _payload(domain: str) -> dict:
    return {
        "evidence_id": f"EV-{domain}",
        "evidence_type": "TEXT",
        "source_id": f"SRC-{domain}",
        "source_type": "DOCUMENT",
        "source_version": "V1",
        "source_ref": f"{domain}:SRC-{domain}@V1",
        "page": 4,
        "section": "Evidence",
        "anchor": "P4-E1",
        "source_text": "A traceable source excerpt.",
        "verification_status": "VERIFIED",
        "evidence_status": "AVAILABLE",
        "created_at": datetime(2026, 9, 26, tzinfo=timezone.utc),
    }


@pytest.mark.parametrize(
    ("domain", "adapter"),
    [
        ("EXTERNAL_SOURCE", map_external_source_evidence),
        ("BUSINESS_OBJECT", map_business_object_evidence),
        ("HISTORICAL_CASE", map_historical_case_evidence),
        ("MAJOR_ISSUE", map_major_issue_evidence),
        ("HARDWARE_CASE", map_hardware_case_evidence),
        ("QUALITY_SCENARIO", map_quality_scenario_evidence),
    ],
)
def test_all_domains_map_to_one_common_contract(domain, adapter):
    evidence = adapter(_payload(domain), producer_object_id=f"OBJ-{domain}", producer_object_version="7")
    assert isinstance(evidence, CommonEvidence)
    assert evidence.contract_version == COMMON_EVIDENCE_CONTRACT_VERSION
    assert evidence.producer_domain == domain
    assert evidence.producer_object_version == "7"
    assert evidence.source.source_id == f"SRC-{domain}"
    assert evidence.source.source_version == "V1"
    assert evidence.locator.page == 4
    assert evidence.locator.anchor == "P4-E1"
    assert evidence.source_text == "A traceable source excerpt."


def test_common_contract_is_strict_and_requires_source_trace():
    with pytest.raises(ValidationError):
        CommonEvidence(**{**_payload("EXTERNAL_SOURCE"), "source_ref": None, "source_reference": None})
    with pytest.raises(ValidationError):
        CommonEvidence(**{**_payload("EXTERNAL_SOURCE"), "unexpected_internal_field": True})


def test_common_contract_preserves_backward_compatible_producer_fields_in_adapter_only():
    payload = _payload("HARDWARE_CASE")
    payload["hardware_locator"] = {"section_path": "Root/Health"}
    payload["hardware_private_field"] = "producer-owned"
    mapped = map_hardware_case_evidence(payload)
    assert mapped.locator.section == "Evidence"
    assert "hardware_private_field" not in mapped.model_dump()


def test_runtime_and_storage_shapes_are_mapped_without_importing_private_models():
    runtime_shape = {
        "evidence_id": "EV-RUNTIME",
        "source": {
            "source_id": "DOC-1",
            "source_type": "PDF",
            "revision": "R2",
            "uri": "knowledge://DOC-1@R2",
        },
        "locator": {"type": "PAGE", "value": {"page": 9, "section": "Health"}},
        "excerpt": "runtime excerpt",
    }
    storage_shape = {
        "evidence_id": "EV-STORAGE",
        "source_id": "DOC-2",
        "source_type": "REPORT",
        "version": "V3",
        "official_url": "https://example.invalid/doc-2",
        "page": 2,
        "section": "Specs",
        "original_text": "storage excerpt",
        "verify_status": "confirmed",
    }
    assert map_external_source_evidence(runtime_shape).source_ref == "knowledge://DOC-1@R2"
    assert map_business_object_evidence(storage_shape).source_ref == "https://example.invalid/doc-2"
