from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_overall_r2_windows_candidate.py"
PREVIOUS_RELEASE = "927efef5b7707d5d2013a34c1f3f40a8ade3d93d"


@pytest.fixture()
def candidate(tmp_path: Path) -> tuple[Path, Path, str, dict[str, str]]:
    completed = subprocess.run(
        [sys.executable, str(BUILDER), "--output-dir", str(tmp_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    values = dict(
        line.split("=", 1)
        for line in completed.stdout.splitlines()
        if "=" in line
    )
    return (
        Path(values["PACKAGE_DIR"]),
        Path(values["PACKAGE_ZIP"]),
        values["DUT_SOURCE_COMMIT"],
        values,
    )


def test_w5_builder_binds_complete_candidate_to_exact_r2_source(candidate):
    package_dir, package_zip, commit, values = candidate
    manifest = json.loads(
        (package_dir / "OVERALL_R2_RELEASE_CANDIDATE_MANIFEST.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(commit) == 40
    assert manifest["contract"] == "overall-r2-release-candidate/v1"
    assert manifest["package_type"] == "COMPLETE_PRODUCT_CANDIDATE"
    assert manifest["dut_source_commit"] == commit
    assert manifest["previous_formal_release"] == PREVIOUS_RELEASE
    assert manifest["product_test_gate"] == "NOT_RUN_FOR_THIS_CANDIDATE"
    assert manifest["release_decision"] == "NOT_REQUESTED"
    assert manifest["development_gate"] == {
        "w1": "BLOCKED_BY_HISTORICAL_BINDING_EVIDENCE",
        "w2": "PASS",
        "w3": "PASS",
        "w4": "PASS",
    }
    workbenches = manifest["current_problem_workbenches"]
    assert workbenches[0] == {
        "label": "问题 / ITR 工作台",
        "route": "/issues",
        "mode": "VERIFIED_PRIOR_FORMAL_CAPABILITY",
        "binding_status": "VERIFIED",
        "evidence_release": PREVIOUS_RELEASE,
    }
    assert [item["route"] for item in workbenches[1:]] == [None, None, None]
    assert [item["binding_status"] for item in workbenches[1:]] == [
        "IMPLEMENTATION_BINDING_PENDING",
        "NEEDS_OWNER_CONFIRMATION",
        "IMPLEMENTATION_BINDING_PENDING",
    ]
    assert manifest["data_binding"]["legacy_quality_issue_db"] == "EXTERNAL_REQUIRED"
    assert manifest["data_binding"]["storage_runtime_db_env"] == "STORAGE_LIFE_RUNTIME_DB"
    assert manifest["data_binding"]["knowledge_repository_env"] == "STORAGE_KNOWLEDGE_REPOSITORY_DIR"
    assert manifest["data_binding"]["knowledge_release_env"] == "STORAGE_KNOWLEDGE_RELEASE_DIR"
    assert manifest["data_binding"]["package_local_runtime_state"] is False
    assert manifest["data_binding"]["package_local_knowledge_state"] is False
    assert manifest["runtime_binding"]["storage_execution_mode"] == "runtime"
    assert manifest["runtime_binding"]["storage_agent_id"] == "storage.emmc.parameter_extract"
    assert manifest["runtime_binding"]["storage_model_ref"] == "qwen_prod"
    assert manifest["data_binding"]["real_internal_data_included"] is False
    assert manifest["provider_binding"]["real_secret_included"] is False
    assert manifest["hardware_maintenance_binding"]["host_role_env"] == "HARDWARE_CASE_HOST_ROLE"
    assert manifest["hardware_maintenance_binding"]["default_role"] == "CONSUMER"
    assert manifest["hardware_maintenance_binding"]["query_parameter_escalation"] is False
    assert manifest["hardware_maintenance_binding"]["consumer_mutation_privilege"] is False
    assert manifest["rollback"]["formal_release_source"] == PREVIOUS_RELEASE
    assert manifest["rollback"]["legacy_db_mutated_by_package"] is False

    assert (package_dir / "INSTALL_OVERALL_R2_WINDOWS.bat").is_file()
    assert (package_dir / "START_OVERALL_R2_WINDOWS.bat").is_file()
    assert (package_dir / "STOP_OVERALL_R2_WINDOWS.bat").is_file()
    assert (package_dir / "CONFIG_OVERALL_R2_WINDOWS.cmd.template").is_file()
    root_bats = sorted(path.name for path in package_dir.glob("*.bat"))
    assert root_bats == [
        "INSTALL_OVERALL_R2_WINDOWS.bat",
        "START_OVERALL_R2_WINDOWS.bat",
        "STOP_OVERALL_R2_WINDOWS.bat",
    ]
    first_readme = (package_dir / "00_README_FIRST.txt").read_text(encoding="utf-8")
    assert "OVERALL R2" in first_readme
    assert "ONLY USER STARTUP PATH" in first_readme
    assert "HARDWARE CASE PRODUCT TEST FULL V0.1" not in first_readme
    assert (package_dir / "quality_knowledge/web/software_assessment_adapter.py").is_file()
    assert (package_dir / "quality_knowledge/web/itr_recovery_adapter.py").is_file()
    assert (package_dir / "quality_knowledge/web/itr_resolution_adapter.py").is_file()
    assert (package_dir / "quality_knowledge/web/missed_test_adapter.py").is_file()
    assert not (package_dir / "CONFIG_OVERALL_R2_WINDOWS.cmd").exists()
    assert (package_dir / "R2_SOURCE_COMMIT").read_text().strip() == commit

    seed = package_dir / "quality_knowledge/config/plc_fields.yaml"
    assert hashlib.sha256(seed.read_bytes()).hexdigest() == (
        "443101e36d2c5b610bc62d130701ed7352175a12696bfc20fd9c5fe47fa411c3"
    )

    with zipfile.ZipFile(package_zip) as bundle:
        assert bundle.testzip() is None
        names = bundle.namelist()
        assert not any("/.git/" in name for name in names)
        assert any(name.endswith("/START_OVERALL_R2_WINDOWS.bat") for name in names)
        assert any(
            name.endswith("/OVERALL_R2_RELEASE_CANDIDATE_MANIFEST.json")
            for name in names
        )

    assert hashlib.sha256(package_zip.read_bytes()).hexdigest() == values["PACKAGE_SHA256"]


def test_w5_internal_checksums_are_complete_and_valid(candidate):
    package_dir, _, _, _ = candidate
    lines = (package_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    indexed = {}
    for line in lines:
        expected, relative = line.split("  ", 1)
        indexed[relative] = expected
        assert hashlib.sha256((package_dir / relative).read_bytes()).hexdigest() == expected

    assert "OVERALL_R2_RELEASE_CANDIDATE_MANIFEST.json" in indexed
    assert "R2_SOURCE_COMMIT" in indexed
    assert "CONFIG_OVERALL_R2_WINDOWS.cmd.template" in indexed
    assert "SHA256SUMS.txt" not in indexed


def test_w5_launcher_fails_closed_without_legacy_database_binding(candidate):
    package_dir, _, _, _ = candidate
    env = os.environ.copy()
    env.pop("LEGACY_QUALITY_ISSUE_DB_PATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            str(package_dir / "scripts/overall_r2_windows_start.py"),
            "--package-root",
            str(package_dir),
            "--check-only",
        ],
        cwd=package_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 6
    assert "LEGACY_DB_PATH_NOT_CONFIGURED" in completed.stderr



def test_w5_clean_external_data_root_seeds_packaged_formal_knowledge_release(tmp_path):
    import importlib.util

    launcher_path = ROOT / "scripts" / "overall_r2_windows_start.py"
    spec = importlib.util.spec_from_file_location("r2_release_seed_launcher", launcher_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    target = tmp_path / "external" / "knowledge_release" / "current"
    result = module._initialize_packaged_knowledge_release(ROOT, target)

    assert result["status"] == "SEEDED_FROM_PACKAGE"
    assert result["knowledge_release_version"] == "KP-STORAGE-RC1-VALIDATION-001"
    assert (target / "release_manifest.json").is_file()
    manifest = json.loads((target / "release_manifest.json").read_text(encoding="utf-8"))
    assert manifest["knowledge_release_version"] == "KP-STORAGE-RC1-VALIDATION-001"

    package_manifest_before = (
        ROOT
        / "products"
        / "storage_rc1"
        / "knowledge_release"
        / "current"
        / "release_manifest.json"
    ).read_bytes()
    second = module._initialize_packaged_knowledge_release(ROOT, target)
    assert second["status"] == "EXISTING_EXTERNAL_RELEASE"
    assert (
        ROOT
        / "products"
        / "storage_rc1"
        / "knowledge_release"
        / "current"
        / "release_manifest.json"
    ).read_bytes() == package_manifest_before


def test_w5_invalid_nonempty_external_release_fails_closed(tmp_path):
    import importlib.util

    launcher_path = ROOT / "scripts" / "overall_r2_windows_start.py"
    spec = importlib.util.spec_from_file_location("r2_invalid_release_launcher", launcher_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    target = tmp_path / "external" / "knowledge_release" / "current"
    target.mkdir(parents=True)
    (target / "stale.txt").write_text("do-not-overwrite", encoding="utf-8")

    with pytest.raises(RuntimeError, match="KNOWLEDGE_RELEASE_TARGET_INVALID_NONEMPTY"):
        module._initialize_packaged_knowledge_release(ROOT, target)
    assert (target / "stale.txt").read_text(encoding="utf-8") == "do-not-overwrite"


def test_w5_extracted_zip_seeds_formal_release_into_clean_external_root(
    candidate,
    tmp_path,
):
    import importlib.util

    _, package_zip, commit, _ = candidate
    extract_root = tmp_path / "unzipped"
    with zipfile.ZipFile(package_zip) as bundle:
        bundle.extractall(extract_root)

    package_root = extract_root / (
        f"OVERALL_R2_COMPLETE_PRODUCT_CANDIDATE_{commit[:12]}_W5"
    )
    assert package_root.is_dir()
    assert (package_root / "R2_SOURCE_COMMIT").read_text().strip() == commit

    launcher_path = package_root / "scripts" / "overall_r2_windows_start.py"
    spec = importlib.util.spec_from_file_location(
        "r2_extracted_release_seed_launcher",
        launcher_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    target = tmp_path / "clean-data" / "knowledge_release" / "current"
    result = module._initialize_packaged_knowledge_release(
        package_root,
        target,
    )

    assert result["status"] == "SEEDED_FROM_PACKAGE"
    assert (
        result["knowledge_release_version"]
        == "KP-STORAGE-RC1-VALIDATION-001"
    )
    manifest = json.loads(
        (target / "release_manifest.json").read_text(encoding="utf-8")
    )
    assert (
        manifest["knowledge_release_version"]
        == "KP-STORAGE-RC1-VALIDATION-001"
    )
    assert (target / "knowledge_objects.json").is_file()
    assert (target / "evidences.json").is_file()
    assert (target / "source_references.json").is_file()

    root_bats = sorted(path.name for path in package_root.glob("*.bat"))
    assert root_bats == [
        "INSTALL_OVERALL_R2_WINDOWS.bat",
        "START_OVERALL_R2_WINDOWS.bat",
        "STOP_OVERALL_R2_WINDOWS.bat",
    ]
    readme = (package_root / "00_README_FIRST.txt").read_text(
        encoding="utf-8"
    )
    assert "ONLY USER STARTUP PATH" in readme
    assert "START_HARDWARE_CASE.bat" not in readme
