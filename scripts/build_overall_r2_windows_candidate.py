"""Build a reproducible Overall R2 Windows Complete Product Candidate."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_FORMAL_RELEASE = "927efef5b7707d5d2013a34c1f3f40a8ade3d93d"

_ROOT_USER_ENTRYPOINTS = {
    "INSTALL_OVERALL_R2_WINDOWS.bat",
    "START_OVERALL_R2_WINDOWS.bat",
    "STOP_OVERALL_R2_WINDOWS.bat",
    "CONFIG_OVERALL_R2_WINDOWS.cmd.template",
}
_ROOT_OBSOLETE_ENTRYPOINTS = {
    "CHECK_ENV.bat",
    "INIT_LOCAL_CONFIG.bat",
    "INIT_LOCAL_CONFIG.sh",
    "RUN_REAL_AI_VALIDATION.bat",
    "RUN_REAL_AI_VALIDATION.sh",
    "START_HARDWARE_CASE.bat",
    "START_HARDWARE_CASE.sh",
    "run.bat",
    "run_hardware_case_mvp_smoke.bat",
    "run_hardware_case_mvp_smoke.sh",
    "run_hardware_case_product_test.bat",
    "run_hardware_case_product_test.sh",
    "run_major_case_mvp_smoke.bat",
    "run_major_case_mvp_smoke.sh",
    "start_quality_capability_p1.bat",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def build(output_dir: Path) -> tuple[Path, Path, str]:
    commit = _git("rev-parse", "HEAD").decode().strip()
    if len(commit) != 40:
        raise RuntimeError("SOURCE_COMMIT_INVALID")

    archive = _git("archive", "--format=zip", "HEAD")
    source_archive_sha = _sha256(archive)
    short = commit[:12]
    package_name = f"OVERALL_R2_COMPLETE_PRODUCT_CANDIDATE_{short}_W5"
    output_dir.mkdir(parents=True, exist_ok=True)
    package_dir = output_dir / package_name
    zip_path = output_dir / f"{package_name}.zip"
    if package_dir.exists() or zip_path.exists():
        raise FileExistsError(f"OUTPUT_ALREADY_EXISTS:{package_name}")

    package_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        source.extractall(package_dir)

    # A Complete Product Candidate must expose one obvious user startup path.
    # Historical product-test launchers remain source-controlled for their own
    # domains, but must not compete at the candidate root.
    for name in sorted(_ROOT_OBSOLETE_ENTRYPOINTS):
        path = package_dir / name
        if path.is_file():
            path.unlink()

    required = (
        "runtime/__init__.py",
        "INSTALL_OVERALL_R2_WINDOWS.bat",
        "START_OVERALL_R2_WINDOWS.bat",
        "STOP_OVERALL_R2_WINDOWS.bat",
        "CONFIG_OVERALL_R2_WINDOWS.cmd.template",
        "scripts/overall_r2_windows_start.py",
        "main.py",
        "requirements.txt",
        "requirements-runtime-p0-test.txt",
    )
    missing = [item for item in required if not (package_dir / item).is_file()]
    if missing:
        raise RuntimeError("PACKAGE_REQUIRED_FILE_MISSING:" + ",".join(missing))

    if (package_dir / "vendor/unified_agent_runtime/runtime/__init__.py").exists():
        raise RuntimeError("DUPLICATE_VENDOR_RUNTIME_IN_SOURCE")

    seed = package_dir / "quality_knowledge/config/plc_fields.yaml"
    expected_seed = "443101e36d2c5b610bc62d130701ed7352175a12696bfc20fd9c5fe47fa411c3"
    actual_seed = _sha256(seed.read_bytes())
    if actual_seed != expected_seed:
        raise RuntimeError(
            f"PLC_SEED_HASH_MISMATCH:expected={expected_seed};actual={actual_seed}"
        )

    (package_dir / "R2_SOURCE_COMMIT").write_text(commit + "\n", encoding="utf-8")
    manifest = {
        "contract": "overall-r2-release-candidate/v1",
        "package_type": "COMPLETE_PRODUCT_CANDIDATE",
        "wave": "W5_RELEASE_ENGINEERING",
        "dut_source_commit": commit,
        "source_archive_sha256": source_archive_sha,
        "previous_formal_release": PREVIOUS_FORMAL_RELEASE,
        "product_test_gate": "NOT_RUN_FOR_THIS_CANDIDATE",
        "release_decision": "NOT_REQUESTED",
        "development_gate": {
            "w1": "BLOCKED_BY_HISTORICAL_BINDING_EVIDENCE",
            "w2": "PASS",
            "w3": "PASS",
            "w4": "PASS",
        },
        "current_problem_workbenches": [
            {
                "label": "问题 / ITR 工作台",
                "route": "/issues",
                "mode": "VERIFIED_PRIOR_FORMAL_CAPABILITY",
                "binding_status": "VERIFIED",
                "evidence_release": PREVIOUS_FORMAL_RELEASE,
            },
            {
                "label": "彻底解决工作台",
                "route": None,
                "mode": "SOURCE_CAPABILITY_BINDING_REQUIRED",
                "binding_status": "IMPLEMENTATION_BINDING_PENDING",
            },
            {
                "label": "软件问题考核工作台",
                "route": None,
                "mode": "SOURCE_CAPABILITY_BINDING_REQUIRED",
                "binding_status": "NEEDS_OWNER_CONFIRMATION",
            },
            {
                "label": "软件问题漏测分析工作台",
                "route": None,
                "mode": "SOURCE_CAPABILITY_BINDING_REQUIRED",
                "binding_status": "IMPLEMENTATION_BINDING_PENDING",
            },
        ],
        "runtime_binding": {
            "mode": "single_shared_package_root",
            "runtime_source": "runtime/__init__.py",
            "commit_marker": "R2_SOURCE_COMMIT",
            "storage_execution_mode": "runtime",
            "storage_agent_id": "storage.emmc.parameter_extract",
            "storage_model_ref": "qwen_prod",
            "duplicate_vendor_runtime": False,
        },
        "startup": {
            "install": "INSTALL_OVERALL_R2_WINDOWS.bat",
            "entry": "START_OVERALL_R2_WINDOWS.bat",
            "stop": "STOP_OVERALL_R2_WINDOWS.bat",
            "command": "main.py knowledge-p1-start",
            "host": "127.0.0.1",
            "port": 8080,
            "default_url": "http://127.0.0.1:8080/p0/overall",
        },
        "data_binding": {
            "legacy_quality_issue_db": "EXTERNAL_REQUIRED",
            "legacy_env": "LEGACY_QUALITY_ISSUE_DB_PATH",
            "r2_data_env": "OVERALL_R2_DATA_DIR",
            "storage_data_env": "STORAGE_LIFE_DATA_DIR",
            "storage_runtime_db_env": "STORAGE_LIFE_RUNTIME_DB",
            "knowledge_repository_env": "STORAGE_KNOWLEDGE_REPOSITORY_DIR",
            "knowledge_release_env": "STORAGE_KNOWLEDGE_RELEASE_DIR",
            "package_local_runtime_state": False,
            "package_local_knowledge_state": False,
            "real_internal_data_included": False,
        },
        "provider_binding": {
            "real_secret_included": False,
            "provider_config_source": "existing config/runtime + environment",
        },
        "hardware_maintenance_binding": {
            "host_role_env": "HARDWARE_CASE_HOST_ROLE",
            "default_role": "CONSUMER",
            "maintainer_role": "MAINTAINER",
            "query_parameter_escalation": False,
            "consumer_mutation_privilege": False,
            "maintenance_entries": [
                "/p0/hardware-cases/base-data",
                "/p0/hardware-cases/intake",
                "/p0/hardware-cases/review",
            ],
        },
        "rollback": {
            "formal_release_source": PREVIOUS_FORMAL_RELEASE,
            "strategy": "restore previous deployment directory and pre-deploy R2 local-data backup",
            "legacy_db_mutated_by_package": False,
        },
        "external_non_code_bindings": [
            "ITR_RESOLUTION_SOURCE_ACTIONS",
            "SOFTWARE_ASSESSMENT_ORIGINAL_BINDING",
        ],
    }
    (package_dir / "OVERALL_R2_RELEASE_CANDIDATE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    readme = """OVERALL R2 — WINDOWS MANUAL VALIDATION CANDIDATE

ONLY USER STARTUP PATH
======================
1. Copy CONFIG_OVERALL_R2_WINDOWS.cmd.template to CONFIG_OVERALL_R2_WINDOWS.cmd.
2. Set LEGACY_QUALITY_ISSUE_DB_PATH to the approved existing Quality Issue DB.
3. Configure only the external Provider environment references required by the
   Active Agent Config revision. Never write a real secret into this package.
4. Double-click START_OVERALL_R2_WINDOWS.bat.
5. Open http://127.0.0.1:8080/p0/overall.
6. Use STOP_OVERALL_R2_WINDOWS.bat to stop the host.

Do NOT use historical Hardware Case / legacy test launchers from older packages.
They are intentionally excluded from this candidate root.

The package starts the existing single FastAPI host through:
  main.py knowledge-p1-start -> create_p0_app

It never creates a second Overall Web/port and does not include real internal
business data or provider secrets. If the Legacy DB binding is missing or
invalid, startup fails closed rather than silently creating substitute data.

Before target deployment, back up the existing R2 local-data directory.
The previous formal product release source is:
  927efef5b7707d5d2013a34c1f3f40a8ade3d93d

This artifact is a Complete Product Candidate. Product Test Gate and Release
Decision do not transfer from the previous release and must be run separately.
"""
    (package_dir / "WINDOWS_START_HERE.txt").write_text(readme, encoding="utf-8")
    (package_dir / "00_README_FIRST.txt").write_text(readme, encoding="utf-8")

    competing = sorted(
        path.name
        for path in package_dir.glob("*.bat")
        if path.name not in _ROOT_USER_ENTRYPOINTS
    )
    if competing:
        raise RuntimeError(
            "COMPETING_ROOT_STARTUP_BAT_PRESENT:" + ",".join(competing)
        )

    files = sorted(p for p in package_dir.rglob("*") if p.is_file())
    sums = "".join(
        f"{_sha256(path.read_bytes())}  {path.relative_to(package_dir).as_posix()}\n"
        for path in files
        if path.name != "SHA256SUMS.txt"
    )
    (package_dir / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")

    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7
    ) as bundle:
        for path in sorted(p for p in package_dir.rglob("*") if p.is_file()):
            relative = path.relative_to(package_dir).as_posix()
            member = zipfile.ZipInfo(
                f"{package_name}/{relative}",
                date_time=(2026, 1, 1, 0, 0, 0),
            )
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            bundle.writestr(
                member,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=7,
            )

    with zipfile.ZipFile(zip_path) as bundle:
        bad = bundle.testzip()
        if bad:
            raise RuntimeError(f"ZIP_MEMBER_FAILED:{bad}")
        packaged = json.loads(
            bundle.read(
                f"{package_name}/OVERALL_R2_RELEASE_CANDIDATE_MANIFEST.json"
            )
        )
        if packaged.get("dut_source_commit") != commit:
            raise RuntimeError("ZIP_SOURCE_BINDING_MISMATCH")

    return package_dir, zip_path, commit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    package_dir, zip_path, commit = build(args.output_dir.expanduser().resolve())
    print(f"DUT_SOURCE_COMMIT={commit}")
    print(f"PACKAGE_ID=OVERALL-R2-CPC-{commit[:12]}-W5")
    print(f"PACKAGE_DIR={package_dir}")
    print(f"PACKAGE_ZIP={zip_path}")
    print(f"PACKAGE_SHA256={_sha256(zip_path.read_bytes())}")
    print("ZIP_INTEGRITY=PASS")
    print("INTERNAL_SHA256SUMS=PASS")
    print("WINDOWS_NATIVE_STARTUP=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
