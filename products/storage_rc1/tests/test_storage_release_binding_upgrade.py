from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_production.release_binding import (
    ReleaseBindingError,
    validate_release_binding,
)
from storage_life import knowledge_release
from storage_life.knowledge_release import KnowledgeReleaseConsumer


def _binding(version: str) -> dict:
    return {
        "binding_contract_version": "knowledge-release-binding/v1.0",
        "storage_product_version": "STORAGE_PRODUCT_MVP_RC1",
        "knowledge_release_version": version,
        "knowledge_object_contract_version": "knowledge-object/v1",
        "knowledge_query_contract_version": "knowledge-query/v1",
        "common_evidence_contract_version": "common-evidence/v1.0",
        "storage_consumer_contract_version": "UKCI-01/V1.0",
        "release_class": "CONTROLLED_CONSUMER_VALIDATION",
        "qualification_state": "VALIDATION_ONLY",
        "compatibility_status": "PASS",
        "latest_floating_dependency": False,
        "direct_knowledge_db_access": False,
        "candidate_store_access": False,
        "storage_self_publish": False,
        "required_behavior": {
            "pinned_release_on_startup": True,
            "fail_closed_on_version_mismatch": True,
            "fail_closed_on_missing_release": True,
            "fail_closed_on_evidence_contract_mismatch": True,
            "upgrade_requires_new_binding": True,
            "rollback_requires_previous_binding": True,
        },
    }


def _manifest(version: str) -> dict:
    return {
        "knowledge_release_version": version,
        "contract_version": "knowledge-query/v1",
        "object_contract_version": "knowledge-object/v1",
        "files": [],
    }


def test_new_explicit_binding_can_pin_a_new_immutable_release():
    binding = _binding("KP-STORAGE-LIFETIME-20261006-R2")

    validate_release_binding(
        binding,
        release_manifest=_manifest(
            "KP-STORAGE-LIFETIME-20261006-R2"
        ),
    )


def test_binding_and_release_version_mismatch_fails_closed():
    with pytest.raises(
        ReleaseBindingError,
        match="RELEASE_VERSION_MISMATCH",
    ):
        validate_release_binding(
            _binding("KP-STORAGE-LIFETIME-20261006-R2"),
            release_manifest=_manifest(
                "KP-STORAGE-LIFETIME-20261006-R3"
            ),
        )


def test_floating_latest_binding_remains_forbidden():
    with pytest.raises(
        ReleaseBindingError,
        match="RELEASE_BINDING_INVALID",
    ):
        validate_release_binding(
            _binding("latest"),
            release_manifest=_manifest("latest"),
        )


def test_storage_consumer_can_select_a_controlled_binding_file(
    tmp_path: Path,
    monkeypatch,
):
    release = tmp_path / "release"
    release.mkdir()
    version = "KP-STORAGE-LIFETIME-20261006-R2"
    (release / "release_manifest.json").write_text(
        json.dumps(_manifest(version)),
        encoding="utf-8",
    )
    for name, payload in (
        ("knowledge_objects.json", {"objects": []}),
        ("evidences.json", {"evidences": []}),
        ("source_references.json", {"source_references": []}),
    ):
        (release / name).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    binding_path = tmp_path / "release_binding_r2.json"
    binding_path.write_text(
        json.dumps(_binding(version)),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "STORAGE_KNOWLEDGE_BINDING_PATH",
        str(binding_path),
    )

    assert knowledge_release._binding_path() == binding_path.resolve()
    selected = KnowledgeReleaseConsumer(
        release
    ).validate_storage_binding()
    assert selected["knowledge_release_version"] == version
