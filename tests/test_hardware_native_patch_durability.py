from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from scripts.hardware_native_patch_durability import (
    GateError,
    NEW_SOURCE,
    OLD_SOURCE,
    STATE_KEYS,
    summarize,
    verify_package_manifest,
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _package_manifest() -> dict[str, Any]:
    return {
        "task": "HARDWARE-R1-NATIVE-PATCH-DURABILITY-GATE-001",
        "package_type": "ZIP",
        "installer_type": "NONE",
        "test_package_not_release": True,
        "old_source_commit": OLD_SOURCE,
        "new_source_commit": NEW_SOURCE,
        "packages": {
            "old": {
                "package_id": "old-test.zip",
                "source_commit": OLD_SOURCE,
                "sha256": "old-hash",
                "size_bytes": 10,
            },
            "new": {
                "package_id": "new-test.zip",
                "source_commit": NEW_SOURCE,
                "sha256": "new-hash",
                "size_bytes": 11,
            },
        },
    }


def _write_pass_evidence(root: Path, package_manifest: dict[str, Any], install_id: str) -> None:
    old_package = package_manifest["packages"]["old"]
    new_package = package_manifest["packages"]["new"]
    sources = {
        "A0210": {
            "source_id": "source-1",
            "source_ref": "source-ref-1",
            "sha256": "source-hash",
            "size_bytes": 12,
            "source_status": "AVAILABLE",
        }
    }
    candidates = {
        "A0162": {
            "candidate_id": "candidate-1",
            "candidate_hash": "candidate-hash",
            "row_version": 2,
            "evidence_ids": ["evidence-1"],
        }
    }
    reviews = {"A0156": {"reviewer": "reviewer", "reviewed_at": "2026-10-04T00:00:00Z"}}
    promotions = {"A0207": {"promotion_status": "VERIFIED", "knowledge_id": "knowledge-1"}}
    formal_refs = [{"business_case_id": "A0207", "knowledge_id": "knowledge-1"}]
    batches = [{"batch_id": "batch-1", "items": [{"candidate_id": "candidate-1"}]}]
    pre = {
        "installation_id": install_id,
        "persistent_data_root": f"/isolated/{install_id}",
        "data_layout_version": 1,
        "hardware_schema_version": 2,
        "asset_schema_version": 3,
        "workbench_schema_version": 1,
        "sources": sources,
        "candidates": candidates,
        "reviews": reviews,
        "promotions": promotions,
        "operation_journal": {"total": 1, "nonterminal": [], "terminal": []},
        "formal_knowledge_refs": formal_refs,
        "batch_count": 1,
        "item_count": 1,
        "batches": batches,
    }
    post = dict(pre)
    states = {key: True for key in STATE_KEYS}
    faults = {key: {"status": "PASS"} for key in ("F1", "F2", "F4", "F5", "F6_F8")}
    faults["F3"] = {"status": "PASS_NOT_APPLICABLE"}
    files = {
        "gate_manifest.json": {
            "status": "PASS",
            "old_source_commit": OLD_SOURCE,
            "new_source_commit": NEW_SOURCE,
            "old_input_kind": "PACKAGE",
            "new_input_kind": "PACKAGE",
            "old_input_id": old_package["package_id"],
            "new_input_id": new_package["package_id"],
            "old_package_sha256": old_package["sha256"],
            "new_package_sha256": new_package["sha256"],
            "installation_id": install_id,
            "persistent_data_root_preserved": True,
            "critical_diff_count": 0,
            "provider_calls_during_patch": 0,
        },
        "pre_patch_state.json": pre,
        "post_patch_state.json": post,
        "identity_diff.json": {"status": "PASS", "critical_diff_count": 0, "diff": []},
        "state_coverage.json": states,
        "source_hash_report.json": {
            key: {field: item.get(field) for field in ("source_id", "source_ref", "sha256", "size_bytes", "source_status")}
            for key, item in sources.items()
        },
        "candidate_hash_report.json": {
            key: {field: item.get(field) for field in ("candidate_id", "candidate_hash", "row_version", "evidence_ids")}
            for key, item in candidates.items()
        },
        "review_report.json": reviews,
        "promotion_report.json": promotions,
        "backup_verify.json": {"backup_state": "PUBLISHED", "verify_status": "PASS"},
        "restore_verify.json": {"status": "PASS", "primary_root_touched": False, "critical_diff_count": 0},
        "provider_call_report.json": {
            "startup_provider_calls": 0,
            "migration_provider_calls": 0,
            "patch_provider_calls": 0,
        },
        "recovery_fault_report.json": faults,
        "startup_trace.json": {
            "persistent_data_root_before": pre["persistent_data_root"],
            "persistent_data_root_after": post["persistent_data_root"],
            "no_second_migration": True,
            "no_empty_db_fallback": True,
            "first_startup": {"ready": True, "migration_id": None},
            "second_startup": {"ready": True, "migration_id": None},
        },
    }
    for filename, value in files.items():
        _write_json(root / filename, value)


def test_summary_passes_only_when_both_platforms_preserve_independent_state(tmp_path: Path) -> None:
    package_manifest = _package_manifest()
    manifest_path = tmp_path / "package_manifest.json"
    _write_json(manifest_path, package_manifest)
    windows = tmp_path / "windows"
    macos = tmp_path / "macos"
    _write_pass_evidence(windows, package_manifest, "windows-install")
    _write_pass_evidence(macos, package_manifest, "macos-install")

    result = summarize(windows, macos, manifest_path, tmp_path / "native_gate_summary.json")

    assert result["native_patch_durability_gate"] == "PASS"
    assert result["windows_critical_diff_count"] == result["macos_critical_diff_count"] == 0
    assert result["windows_provider_calls"] == result["macos_provider_calls"] == 0
    assert result["installer_certification"] == "NOT_RUN"
    assert result["formal_release_package_certification"] == "NOT_RUN"


@pytest.mark.parametrize("platform", ["windows", "macos"])
def test_summary_blocks_nonzero_identity_diff(tmp_path: Path, platform: str) -> None:
    package_manifest = _package_manifest()
    manifest_path = tmp_path / "package_manifest.json"
    _write_json(manifest_path, package_manifest)
    windows = tmp_path / "windows"
    macos = tmp_path / "macos"
    _write_pass_evidence(windows, package_manifest, "windows-install")
    _write_pass_evidence(macos, package_manifest, "macos-install")
    chosen = windows if platform == "windows" else macos
    diff = json.loads((chosen / "identity_diff.json").read_text(encoding="utf-8"))
    diff.update(status="BLOCKED", critical_diff_count=1, diff=[{"path": "candidate_id"}])
    _write_json(chosen / "identity_diff.json", diff)

    result = summarize(windows, macos, manifest_path, tmp_path / "summary.json")

    assert result["native_patch_durability_gate"] == "BLOCKED"
    assert result["platforms"][platform]["status"] == "BLOCKED"


def test_verify_package_manifest_binds_exact_zip_hash_and_commit(tmp_path: Path) -> None:
    manifest = _package_manifest()
    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    for role in ("old", "new"):
        item = manifest["packages"][role]
        path = package_dir / item["package_id"]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("application/services/hardware_startup_coordinator.py", "pass\n")
            archive.writestr("application/services/hardware_case_r1_workbench.py", "pass\n")
            if role == "new":
                archive.writestr("application/tests/test_hardware_patch_durability.py", "pass\n")
        item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        item["size_bytes"] = path.stat().st_size
    manifest_path = tmp_path / "package_manifest.json"
    _write_json(manifest_path, manifest)

    verified = verify_package_manifest(manifest_path, package_dir)

    assert verified["old_source_commit"] == OLD_SOURCE
    assert verified["new_source_commit"] == NEW_SOURCE


def test_verify_package_manifest_rejects_a_mutated_package(tmp_path: Path) -> None:
    manifest = _package_manifest()
    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    for role in ("old", "new"):
        item = manifest["packages"][role]
        path = package_dir / item["package_id"]
        path.write_bytes(b"not the committed ZIP")
        item["size_bytes"] = path.stat().st_size
    manifest_path = tmp_path / "package_manifest.json"
    _write_json(manifest_path, manifest)

    with pytest.raises(GateError, match="PACKAGE_SHA256_MISMATCH"):
        verify_package_manifest(manifest_path, package_dir)
