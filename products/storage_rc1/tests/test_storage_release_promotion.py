from __future__ import annotations

import json
from pathlib import Path

from storage_life import knowledge_product
from storage_life import knowledge_release
from storage_life.knowledge_release import KnowledgeReleaseConsumer


def _manifest(version: str) -> dict:
    return {
        "knowledge_release_version": version,
        "contract_version": "knowledge-query/v1",
        "object_contract_version": "knowledge-object/v1",
        "snapshot_hash": f"sha-{version}",
        "object_count": 1,
        "evidence_count": 1,
        "source_reference_count": 1,
        "files": [],
    }


def _write_release(root: Path, version: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "release_manifest.json").write_text(
        json.dumps(_manifest(version)),
        encoding="utf-8",
    )
    (root / "knowledge_objects.json").write_text(
        json.dumps({"objects": []}),
        encoding="utf-8",
    )
    (root / "evidences.json").write_text(
        json.dumps({"evidences": []}),
        encoding="utf-8",
    )
    (root / "source_references.json").write_text(
        json.dumps({"source_references": []}),
        encoding="utf-8",
    )


def test_explicit_promotion_switches_release_and_binding_together(
    tmp_path: Path,
    monkeypatch,
) -> None:
    release_root = tmp_path / "knowledge_release"
    current = release_root / "current"
    candidate = (
        release_root
        / "candidates"
        / "KP-STORAGE-LIFETIME-20261006-R2"
    )
    _write_release(current, "KP-STORAGE-RC1-VALIDATION-001")
    _write_release(candidate, "KP-STORAGE-LIFETIME-20261006-R2")

    monkeypatch.setenv(
        "STORAGE_KNOWLEDGE_RELEASE_DIR",
        str(current),
    )
    monkeypatch.delenv(
        "STORAGE_KNOWLEDGE_BINDING_PATH",
        raising=False,
    )

    result = knowledge_product.promote_release_candidate(
        "KP-STORAGE-LIFETIME-20261006-R2",
        approved_by="storage-reviewer",
    )

    assert result["status"] == "READY"
    assert (
        result["knowledge_release_version"]
        == "KP-STORAGE-LIFETIME-20261006-R2"
    )
    assert result["binding_mode"] == "EXPLICIT_CONTROLLED_BINDING"

    active_manifest = json.loads(
        (current / "release_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    active_binding = json.loads(
        (current / "release_binding.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        active_manifest["knowledge_release_version"]
        == "KP-STORAGE-LIFETIME-20261006-R2"
    )
    assert (
        active_binding["knowledge_release_version"]
        == "KP-STORAGE-LIFETIME-20261006-R2"
    )
    assert (
        active_binding["promotion"]["approved_by"]
        == "storage-reviewer"
    )
    assert active_binding["latest_floating_dependency"] is False
    assert active_binding["storage_self_publish"] is False

    archived = (
        release_root
        / "approved"
        / "KP-STORAGE-RC1-VALIDATION-001"
    )
    assert archived.is_dir()
    assert (archived / "release_manifest.json").is_file()
    assert (archived / "release_binding.json").is_file()

    consumer = KnowledgeReleaseConsumer.current()
    selected = consumer.validate_storage_binding()
    assert (
        selected["knowledge_release_version"]
        == "KP-STORAGE-LIFETIME-20261006-R2"
    )


def test_candidate_exists_without_promotion_does_not_change_current(
    tmp_path: Path,
    monkeypatch,
) -> None:
    release_root = tmp_path / "knowledge_release"
    current = release_root / "current"
    candidate = (
        release_root
        / "candidates"
        / "KP-STORAGE-LIFETIME-20261006-R2"
    )
    _write_release(current, "KP-STORAGE-RC1-VALIDATION-001")
    _write_release(candidate, "KP-STORAGE-LIFETIME-20261006-R2")
    monkeypatch.setenv(
        "STORAGE_KNOWLEDGE_RELEASE_DIR",
        str(current),
    )

    active = json.loads(
        (current / "release_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        active["knowledge_release_version"]
        == "KP-STORAGE-RC1-VALIDATION-001"
    )
    assert not (current / "release_binding.json").exists()



def _binding_template(version: str = "KP-STORAGE-RC1-VALIDATION-001") -> dict:
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


def test_default_binding_template_resolves_from_effective_packaged_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "STORAGE_PRODUCT_MVP_RC1"
    binding_path = (
        package_root
        / "contracts"
        / "release_binding"
        / "v1"
        / "release_binding.json"
    )
    binding_path.parent.mkdir(parents=True)
    expected = _binding_template()
    binding_path.write_text(json.dumps(expected), encoding="utf-8")

    monkeypatch.setattr(
        knowledge_product,
        "project_root",
        lambda: package_root,
    )

    assert knowledge_product._default_binding_template() == expected


def test_binding_path_finds_default_contract_in_flat_packaged_layout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "STORAGE_PRODUCT_MVP_RC1"
    fake_module = package_root / "storage_life" / "knowledge_release.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("# packaged module marker\n", encoding="utf-8")

    binding_path = (
        package_root
        / "contracts"
        / "release_binding"
        / "v1"
        / "release_binding.json"
    )
    binding_path.parent.mkdir(parents=True)
    binding_path.write_text(
        json.dumps(_binding_template()),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        knowledge_release,
        "__file__",
        str(fake_module),
    )
    monkeypatch.delenv(
        "STORAGE_KNOWLEDGE_BINDING_PATH",
        raising=False,
    )

    assert knowledge_release._binding_path() == binding_path.resolve()
