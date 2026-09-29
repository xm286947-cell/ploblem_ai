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
    assert manifest["data_binding"]["legacy_quality_issue_db"] == "EXTERNAL_REQUIRED"
    assert manifest["data_binding"]["real_internal_data_included"] is False
    assert manifest["provider_binding"]["real_secret_included"] is False
    assert manifest["rollback"]["formal_release_source"] == PREVIOUS_RELEASE
    assert manifest["rollback"]["legacy_db_mutated_by_package"] is False

    assert (package_dir / "INSTALL_OVERALL_R2_WINDOWS.bat").is_file()
    assert (package_dir / "START_OVERALL_R2_WINDOWS.bat").is_file()
    assert (package_dir / "STOP_OVERALL_R2_WINDOWS.bat").is_file()
    assert (package_dir / "CONFIG_OVERALL_R2_WINDOWS.cmd.template").is_file()
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
