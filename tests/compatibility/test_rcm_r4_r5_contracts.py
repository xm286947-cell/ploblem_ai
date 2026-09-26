from __future__ import annotations

import json
from pathlib import Path

import pytest

from compatibility.common_evidence import (
    COMMON_EVIDENCE_CONTRACT_VERSION,
    CommonEvidenceContractError,
    map_common_evidence,
    validate_common_evidence,
)
from knowledge_production.release_binding import (
    REQUIRED_BINDING,
    ReleaseBindingError,
    validate_release_binding,
)


ROOT = Path(__file__).resolve().parents[2]
COMMON_SCHEMA = json.loads(
    (ROOT / "contracts/common_evidence/v1/common_evidence_contract.schema.json").read_text()
)
BINDING = json.loads(
    (ROOT / "contracts/release_binding/v1/release_binding.json").read_text()
)
RELEASE_MANIFEST = json.loads(
    (ROOT / "products/storage_rc1/knowledge_release/current/release_manifest.json").read_text()
)


def test_r4_has_one_frozen_public_common_evidence_contract():
    assert COMMON_EVIDENCE_CONTRACT_VERSION == "common-evidence/v1.0"
    assert COMMON_SCHEMA["properties"]["contract_version"]["const"] == COMMON_EVIDENCE_CONTRACT_VERSION
    assert set(COMMON_SCHEMA["required"]) >= {
        "evidence_id",
        "evidence_type",
        "source",
        "locator",
        "excerpt",
        "source_text",
        "content_hash",
        "source_ref",
        "source_reference",
        "producer_domain",
        "producer_object_id",
        "producer_object_version",
        "verification_status",
        "evidence_status",
        "created_at",
    }


def test_r4_maps_existing_knowledge_evidence_without_internal_coupling():
    raw = json.loads(
        (ROOT / "products/storage_rc1/knowledge_release/current/evidences.json").read_text()
    )["evidences"][0]
    mapped = map_common_evidence(
        raw,
        producer_domain="External Source",
        producer_object_id="KO-4ad47002b1ba4070fa499559",
        producer_object_version=1,
    )
    validate_common_evidence(mapped)
    assert mapped["contract_version"] == "common-evidence/v1.0"
    assert mapped["source"]["source_id"] == "NVME"
    assert mapped["source"]["source_version"] == "2.0d"
    assert mapped["locator"]["page"] == 200
    assert mapped["locator"]["section"] == "SMART / Health"
    assert "internal_path" not in json.dumps(mapped)
    assert "row_id" not in json.dumps(mapped)
    assert "vector_id" not in json.dumps(mapped)


@pytest.mark.parametrize("domain", [
    "External Source",
    "Business Object",
    "Historical Case",
    "Major Issue",
    "Hardware Case",
    "Quality Scenario",
])
def test_r4_domain_adapters_share_one_contract(domain):
    mapped = map_common_evidence(
        {
            "evidence_id": f"E-{domain.replace(' ', '-')}",
            "evidence_type": "TEXT",
            "source": {"source_type": "BUSINESS", "source_id": "SRC-1", "source_version": "V1"},
            "locator": {"value": {"section": "fixture"}},
            "excerpt": "explicit source excerpt",
        },
        producer_domain=domain,
        producer_object_id="OBJ-1",
        producer_object_version=1,
    )
    validate_common_evidence(mapped)
    assert mapped["contract_version"] == COMMON_EVIDENCE_CONTRACT_VERSION
    assert mapped["producer_domain"] == domain


def test_r4_missing_locator_values_remain_null():
    mapped = map_common_evidence(
        {"evidence_id": "E-NULL", "evidence_type": "TEXT"},
        producer_domain="Hardware Case",
    )
    assert mapped["locator"] == {"page": None, "section": None, "anchor": None}
    assert mapped["source_reference"] is None
    validate_common_evidence(mapped)


def test_r4_rejects_missing_evidence_identity():
    with pytest.raises(CommonEvidenceContractError):
        map_common_evidence({"evidence_type": "TEXT"}, producer_domain="Major Issue")


def test_r5_binding_matches_pinned_release_manifest():
    validate_release_binding(BINDING, release_manifest=RELEASE_MANIFEST)
    assert {key: BINDING[key] for key in REQUIRED_BINDING} == REQUIRED_BINDING
    assert BINDING["knowledge_release_version"] != "latest"
    assert BINDING["release_class"] == "CONTROLLED_CONSUMER_VALIDATION"
    assert BINDING["qualification_state"] == "VALIDATION_ONLY"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("knowledge_release_version", "latest", "RELEASE_VERSION_MISMATCH"),
        ("common_evidence_contract_version", "knowledge-evidence/v1", "RELEASE_VERSION_MISMATCH"),
        ("latest_floating_dependency", True, "LATEST_FLOATING_DEPENDENCY"),
    ],
)
def test_r5_fails_closed_on_incompatible_binding(field, value, code):
    invalid = dict(BINDING)
    invalid[field] = value
    with pytest.raises(ReleaseBindingError) as caught:
        validate_release_binding(invalid, release_manifest=RELEASE_MANIFEST)
    assert caught.value.code == code


def test_r5_fails_closed_when_release_is_missing():
    with pytest.raises(ReleaseBindingError) as caught:
        validate_release_binding(BINDING, release_manifest=None)
    assert caught.value.code == "KNOWLEDGE_RELEASE_NOT_FOUND"


def test_r5_fails_closed_when_manifest_versions_do_not_match():
    invalid_manifest = dict(RELEASE_MANIFEST)
    invalid_manifest["object_contract_version"] = "knowledge-object/v2"
    with pytest.raises(ReleaseBindingError) as caught:
        validate_release_binding(BINDING, release_manifest=invalid_manifest)
    assert caught.value.code == "RELEASE_VERSION_MISMATCH"
