from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts import build_hardware_r1_asset_durability_formal_zip as release
from scripts import hardware_r1_asset_durability_formal_zip_smoke as smoke


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict[str, object]]:
    package_id = f"HARDWARE_R1_ASSET_DURABILITY_FORMAL_{release.SOURCE_BASE[:12]}.zip"
    artifact_metadata = {
        name: {
            "id": release.D2_ARTIFACT_IDS[name],
            "name": name,
            "expired": False,
            "digest": f"sha256:{index:064x}",
            "size_in_bytes": index + 100,
            "created_at": "2026-10-04T10:00:00Z",
            "expires_at": "2026-10-18T10:00:00Z",
        }
        for index, name in enumerate(release.D2_ARTIFACT_NAMES, start=1)
    }
    binding = {
        "run_id": release.D2_RUN_ID,
        "head_sha": release.D2_HEAD,
        "conclusion": "success",
        "run_url": f"https://github.com/xm286947-cell/ploblem_ai/actions/runs/{release.D2_RUN_ID}",
        "artifacts": artifact_metadata,
    }
    binding_path = tmp_path / "d2_evidence_binding.json"
    binding_path.write_bytes(_json_bytes(binding))

    runtime_bytes = b"# frozen runtime entry\n"
    runtime_entry = {
        "path": "runtime/start.py",
        "sha256": _sha(runtime_bytes),
        "size_bytes": len(runtime_bytes),
    }
    summary_bytes = _json_bytes({"native_patch_durability_gate": "PASS"})
    embedded_binding = {
        "run_id": release.D2_RUN_ID,
        "head_sha": release.D2_HEAD,
        "artifact_ids": {name: artifact_metadata[name]["id"] for name in release.D2_ARTIFACT_NAMES},
        "artifact_metadata": artifact_metadata,
        "scope": "HARDWARE_R1_ASSET_DURABILITY",
    }
    evidence = {
        "native_gate_summary.json": summary_bytes,
        "binding.json": _json_bytes(embedded_binding),
    }
    evidence_entries = [
        {"path": name, "sha256": _sha(payload), "size_bytes": len(payload)}
        for name, payload in evidence.items()
    ]
    content_manifest = {
        "task": release.TASK,
        "CERTIFICATION_STATUS": "PENDING_NATIVE_STARTUP_BINDING",
        "CERTIFICATION_SCOPE": "HARDWARE_R1_ASSET_DURABILITY",
        "package_id": package_id,
        "source_commit": release.SOURCE_BASE,
        "certification_scope": "HARDWARE_R1_ASSET_DURABILITY",
        "runtime_payload_sha256": release._tree_digest([runtime_entry]),
        "runtime_payload_inventory": [runtime_entry],
        "d2_tested_runtime_payload_matches": True,
        "d2_evidence_tree_sha256": release._tree_digest(evidence_entries),
        "d2_native_gate_summary_sha256": _sha(summary_bytes),
        "d2_artifact_ids": {name: artifact_metadata[name]["id"] for name in release.D2_ARTIFACT_NAMES},
        "d2_artifact_metadata": artifact_metadata,
        "d2_evidence_files": evidence_entries,
        "nonclaims": {
            "hardware_case_mvp_release": "NOT_CERTIFIED",
            "product_release_claim": "NOT_MADE",
            "installer_certification": "NOT_RUN",
            "test_package_not_release": "NOT_REUSED_AS_RELEASE",
        },
    }
    archive_path = tmp_path / package_id
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("runtime/start.py", runtime_bytes)
        archive.writestr("RELEASE_CONTENT_MANIFEST.json", _json_bytes(content_manifest))
        for name, payload in evidence.items():
            archive.writestr("D2_EVIDENCE/" + name, payload)
    package_sha = release._sha256(archive_path)
    (tmp_path / f"{package_id}.sha256").write_text(f"{package_sha}  {package_id}\n", encoding="ascii")
    build = {
        "task": release.TASK,
        "CERTIFICATION_STATUS": "PENDING_NATIVE_STARTUP_BINDING",
        "CERTIFICATION_SCOPE": "HARDWARE_R1_ASSET_DURABILITY",
        "package_id": package_id,
        "package_sha256": package_sha,
        "package_size_bytes": archive_path.stat().st_size,
        "source_commit": release.SOURCE_BASE,
        "certification_scope": "HARDWARE_R1_ASSET_DURABILITY",
        "runtime_payload_sha256": content_manifest["runtime_payload_sha256"],
        "d2_tested_runtime_payload_sha256": content_manifest["runtime_payload_sha256"],
        "d2_run_id": release.D2_RUN_ID,
        "d2_head_sha": release.D2_HEAD,
        "d2_evidence_tree_sha256": content_manifest["d2_evidence_tree_sha256"],
        "d2_native_gate_summary_sha256": content_manifest["d2_native_gate_summary_sha256"],
        "d2_artifact_ids": content_manifest["d2_artifact_ids"],
        "d2_artifact_metadata": artifact_metadata,
    }
    build_path = tmp_path / "package_build_manifest.json"
    build_path.write_bytes(_json_bytes(build))

    report_base = {
        "status": "PASS",
        "package_id": package_id,
        "package_sha256": package_sha,
        "source_commit": release.SOURCE_BASE,
        "fresh_extract": True,
        "persistent_data_root_isolated": True,
        "persistent_data_root": "/isolated/data",
        "startup_ready": True,
        "endpoints": {
            "/health": 200,
            "/ready": 200,
            "/api/system/hardware/startup": 200,
            "/p0/hardware-cases": 200,
        },
    }
    windows_path = tmp_path / "windows_startup_report.json"
    macos_path = tmp_path / "macos_startup_report.json"
    windows_path.write_bytes(_json_bytes({**report_base, "platform": "windows", "persistent_data_root": "C:/isolated/data", "installation_id": "win-install"}))
    macos_path.write_bytes(_json_bytes({**report_base, "platform": "macos", "persistent_data_root": "/isolated/data", "installation_id": "mac-install"}))
    return build_path, binding_path, windows_path, macos_path, build


def test_finalizer_binds_scoped_certification_and_nonclaims(tmp_path: Path) -> None:
    build_path, binding_path, windows_path, macos_path, _ = _fixture(tmp_path)

    manifest = release.finalize_release_manifest(
        build_path,
        binding_path,
        windows_path,
        macos_path,
        tmp_path / "RELEASE_MANIFEST.json",
    )

    assert manifest["CERTIFICATION_STATUS"] == "PASS"
    assert manifest["CERTIFICATION_SCOPE"] == "HARDWARE_R1_ASSET_DURABILITY"
    assert manifest["HARDWARE_CASE_MVP_RELEASE"] == "NOT_CERTIFIED"
    assert manifest["PRODUCT_RELEASE_CLAIM"] == "NOT_MADE"
    assert manifest["INSTALLER_CERTIFICATION"] == "NOT_RUN"
    assert manifest["TEST_PACKAGE_NOT_RELEASE"] == "NOT_REUSED_AS_RELEASE"
    assert manifest["WINDOWS_NATIVE_STARTUP"] == "PASS"
    assert manifest["MACOS_NATIVE_STARTUP"] == "PASS"


def test_finalizer_rejects_same_installation_identity(tmp_path: Path) -> None:
    build_path, binding_path, windows_path, macos_path, _ = _fixture(tmp_path)
    report = json.loads(macos_path.read_text(encoding="utf-8"))
    report["installation_id"] = "win-install"
    macos_path.write_bytes(_json_bytes(report))

    with pytest.raises(release.GateError, match="NATIVE_INSTALLATION_OR_DATA_ROOTS_NOT_INDEPENDENT"):
        release.finalize_release_manifest(
            build_path,
            binding_path,
            windows_path,
            macos_path,
            tmp_path / "RELEASE_MANIFEST.json",
        )


def test_finalizer_rejects_package_hash_drift(tmp_path: Path) -> None:
    build_path, binding_path, windows_path, macos_path, build = _fixture(tmp_path)
    archive_path = tmp_path / str(build["package_id"])
    archive_path.write_bytes(archive_path.read_bytes() + b"tampered")

    with pytest.raises(release.GateError, match="RELEASE_PACKAGE_HASH_MISMATCH"):
        release.finalize_release_manifest(
            build_path,
            binding_path,
            windows_path,
            macos_path,
            tmp_path / "RELEASE_MANIFEST.json",
        )


def test_finalizer_rejects_non_ready_or_wrong_scope_startup(tmp_path: Path) -> None:
    build_path, binding_path, windows_path, macos_path, _ = _fixture(tmp_path)
    report = json.loads(windows_path.read_text(encoding="utf-8"))
    report["startup_ready"] = False
    windows_path.write_bytes(_json_bytes(report))

    with pytest.raises(release.GateError, match="NATIVE_STARTUP_ISOLATION_FAILED:windows"):
        release.finalize_release_manifest(
            build_path,
            binding_path,
            windows_path,
            macos_path,
            tmp_path / "RELEASE_MANIFEST.json",
        )


def test_smoke_verifies_embedded_runtime_inventory(tmp_path: Path) -> None:
    build_path, _, _, _, build = _fixture(tmp_path)
    package_id = str(build["package_id"])
    extract_root = tmp_path / "extract"
    extract_root.mkdir()
    with zipfile.ZipFile(tmp_path / package_id) as archive:
        archive.extractall(extract_root)

    manifest = smoke._verify_content(extract_root, package_id, str(build["package_sha256"]))
    assert manifest["certification_scope"] == "HARDWARE_R1_ASSET_DURABILITY"

    (extract_root / "runtime/start.py").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="RUNTIME_PAYLOAD_HASH_MISMATCH"):
        smoke._verify_content(extract_root, package_id, str(build["package_sha256"]))
