from __future__ import annotations

import json
from pathlib import Path

from storage_life import knowledge_product
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
