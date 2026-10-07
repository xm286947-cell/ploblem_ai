#!/usr/bin/env python3
"""Build exact-source test ZIPs and summarize native D1 Harness evidence.

This D2 acceptance wrapper deliberately leaves the frozen D1 Harness
unchanged. ZIPs are source-bound test packages, not installer or release
certification artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


TASK = "HARDWARE-R1-NATIVE-PATCH-DURABILITY-GATE-001"
OLD_SOURCE = "4a9cfdfc2c10366c86ab01efcc1c236da5942d38"
NEW_SOURCE = "87fcfaede66f4565eb3fba12330f7b8fba51187a"
REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_KEYS = (
    "S1_SOURCE_ONLY",
    "S2_CANDIDATE_READY",
    "S3_PRODUCTION_REVIEW_REQUIRED",
    "S4_PRODUCTION_REVIEW_RESOLVED",
    "S5_PROMOTION_IN_PROGRESS",
    "S6_FORMAL_PUBLISHED",
    "S7_VERIFIED",
)
FAULT_KEYS = ("F1", "F2", "F4", "F5", "F6_F8")
CRITICAL_STATE_KEYS = (
    "installation_id",
    "persistent_data_root",
    "data_layout_version",
    "hardware_schema_version",
    "asset_schema_version",
    "workbench_schema_version",
    "sources",
    "candidates",
    "reviews",
    "promotions",
    "operation_journal",
    "formal_knowledge_refs",
    "batch_count",
    "item_count",
    "batches",
)
EVIDENCE_FILES = (
    "gate_manifest.json",
    "pre_patch_state.json",
    "post_patch_state.json",
    "identity_diff.json",
    "source_hash_report.json",
    "candidate_hash_report.json",
    "review_report.json",
    "promotion_report.json",
    "backup_verify.json",
    "restore_verify.json",
    "startup_trace.json",
    "provider_call_report.json",
    "recovery_fault_report.json",
)


class GateError(RuntimeError):
    pass


def _json_read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GateError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_commit(commit: str, repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", f"{commit}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    resolved = result.stdout.strip()
    if resolved != commit:
        raise GateError(f"SOURCE_COMMIT_MISMATCH:{commit}:{resolved}")
    return resolved


def _validate_zip(path: Path, *, require_d1_harness: bool) -> None:
    if not zipfile.is_zipfile(path):
        raise GateError(f"PACKAGE_NOT_ZIP:{path.name}")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in names:
            member = PurePosixPath(name)
            parts = tuple(part for part in member.parts if part not in {"", "."})
            if member.is_absolute() or ".." in parts:
                raise GateError(f"PACKAGE_PATH_INVALID:{name}")
        required = {
            "application/services/hardware_startup_coordinator.py",
            "application/services/hardware_case_r1_workbench.py",
        }
        if require_d1_harness:
            required.add("application/tests/test_hardware_patch_durability.py")
        missing = sorted(required - set(names))
        if missing:
            raise GateError("PACKAGE_CONTENT_MISSING:" + ",".join(missing))


def build_packages(
    output_dir: Path,
    repo_root: Path = REPO_ROOT,
    *,
    old_source: str = OLD_SOURCE,
    new_source: str = NEW_SOURCE,
) -> dict[str, Any]:
    old_commit = _resolve_commit(str(old_source), repo_root)
    new_commit = _resolve_commit(str(new_source), repo_root)
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise GateError("PACKAGE_OUTPUT_DIR_MUST_BE_EMPTY_OR_NONEXISTENT")
    output_dir.mkdir(parents=True, exist_ok=True)

    packages: dict[str, dict[str, Any]] = {}
    for role, commit in (("old", old_commit), ("new", new_commit)):
        package_id = f"HARDWARE_R1_NATIVE_PATCH_DURABILITY_{role.upper()}_{commit[:12]}.zip"
        package_path = output_dir / package_id
        if package_path.exists():
            raise GateError(f"PACKAGE_ALREADY_EXISTS:{package_id}")
        subprocess.run(
            [
                "git", "-C", str(repo_root), "archive", "--format=zip",
                "--prefix=application/", f"--output={package_path}", commit,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        _validate_zip(package_path, require_d1_harness=(role == "new"))
        package_hash = _sha256(package_path)
        (output_dir / f"{package_id}.sha256").write_text(
            f"{package_hash}  {package_id}\n", encoding="ascii"
        )
        packages[role] = {
            "package_id": package_id,
            "source_commit": commit,
            "sha256": package_hash,
            "size_bytes": package_path.stat().st_size,
        }

    manifest = {
        "contract_version": "hardware-native-patch-test-packages/v1",
        "task": TASK,
        "package_type": "ZIP",
        "installer_type": "NONE",
        "test_package_not_release": True,
        "installer_certification": "NOT_RUN",
        "formal_release_package_certification": "NOT_RUN",
        "old_source_commit": old_commit,
        "new_source_commit": new_commit,
        "packages": packages,
    }
    _json_write(output_dir / "package_manifest.json", manifest)
    return manifest


def verify_package_manifest(
    manifest_path: Path,
    package_dir: Path,
    *,
    expected_old_source: str = OLD_SOURCE,
    expected_new_source: str = NEW_SOURCE,
) -> dict[str, Any]:
    manifest = _json_read(manifest_path)
    if manifest.get("task") != TASK:
        raise GateError("PACKAGE_TASK_MISMATCH")
    if (
        manifest.get("package_type") != "ZIP"
        or manifest.get("installer_type") != "NONE"
        or manifest.get("test_package_not_release") is not True
    ):
        raise GateError("PACKAGE_ACCEPTANCE_SCOPE_MISMATCH")
    if (
        manifest.get("old_source_commit") != expected_old_source
        or manifest.get("new_source_commit") != expected_new_source
    ):
        raise GateError("PACKAGE_SOURCE_COMMITS_MISMATCH")
    for role in ("old", "new"):
        entry = manifest.get("packages", {}).get(role)
        if not isinstance(entry, dict):
            raise GateError(f"PACKAGE_ENTRY_MISSING:{role}")
        expected_commit = (
            expected_old_source if role == "old" else expected_new_source
        )
        if entry.get("source_commit") != expected_commit:
            raise GateError(f"PACKAGE_SOURCE_BINDING_MISMATCH:{role}")
        package_id = str(entry.get("package_id") or "")
        if not package_id or Path(package_id).name != package_id:
            raise GateError(f"PACKAGE_ID_INVALID:{role}")
        package_path = package_dir / package_id
        if not package_path.is_file() or _sha256(package_path) != entry.get("sha256"):
            raise GateError(f"PACKAGE_SHA256_MISMATCH:{role}")
        if package_path.stat().st_size != entry.get("size_bytes"):
            raise GateError(f"PACKAGE_SIZE_MISMATCH:{role}")
        _validate_zip(package_path, require_d1_harness=(role == "new"))
    return manifest


def _platform_result(
    name: str,
    evidence_dir: Path,
    package_manifest: dict[str, Any],
    *,
    expected_old_source: str,
    expected_new_source: str,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        missing = [filename for filename in EVIDENCE_FILES if not (evidence_dir / filename).is_file()]
        if missing:
            raise GateError("EVIDENCE_FILES_MISSING:" + ",".join(missing))

        gate = _json_read(evidence_dir / "gate_manifest.json")
        pre = _json_read(evidence_dir / "pre_patch_state.json")
        post = _json_read(evidence_dir / "post_patch_state.json")
        diff = _json_read(evidence_dir / "identity_diff.json")
        states = _json_read(evidence_dir / "state_coverage.json")
        backup = _json_read(evidence_dir / "backup_verify.json")
        restore = _json_read(evidence_dir / "restore_verify.json")
        providers = _json_read(evidence_dir / "provider_call_report.json")
        faults = _json_read(evidence_dir / "recovery_fault_report.json")
        startup = _json_read(evidence_dir / "startup_trace.json")
        source_hashes = _json_read(evidence_dir / "source_hash_report.json")
        candidate_hashes = _json_read(evidence_dir / "candidate_hash_report.json")
        review_report = _json_read(evidence_dir / "review_report.json")
        promotion_report = _json_read(evidence_dir / "promotion_report.json")

        expected_packages = package_manifest["packages"]
        checks: dict[str, bool] = {
            "gate_pass": gate.get("status") == "PASS",
            "source_commits_bound": (
                gate.get("old_source_commit") == expected_old_source
                and gate.get("new_source_commit") == expected_new_source
            ),
            "packages_bound": (
                gate.get("old_input_kind") == "PACKAGE"
                and gate.get("new_input_kind") == "PACKAGE"
                and gate.get("old_input_id") == expected_packages["old"]["package_id"]
                and gate.get("new_input_id") == expected_packages["new"]["package_id"]
                and gate.get("old_package_sha256") == expected_packages["old"]["sha256"]
                and gate.get("new_package_sha256") == expected_packages["new"]["sha256"]
            ),
            "installation_id_preserved": (
                pre.get("installation_id") == post.get("installation_id") == gate.get("installation_id")
            ),
            "data_root_preserved": (
                pre.get("persistent_data_root") == post.get("persistent_data_root")
                and startup.get("persistent_data_root_before") == startup.get("persistent_data_root_after")
                and gate.get("persistent_data_root_preserved") is True
            ),
            "source_identity_preserved": pre.get("sources") == post.get("sources"),
            "source_hash_report_matches": source_hashes == {
                case_id: {
                    key: value.get(key)
                    for key in ("source_id", "source_ref", "sha256", "size_bytes", "source_status")
                }
                for case_id, value in post.get("sources", {}).items()
            },
            "candidate_identity_preserved": pre.get("candidates") == post.get("candidates"),
            "candidate_hash_report_matches": candidate_hashes == {
                case_id: {
                    key: value.get(key)
                    for key in ("candidate_id", "candidate_hash", "row_version", "evidence_ids")
                }
                for case_id, value in post.get("candidates", {}).items()
            },
            "review_preserved": pre.get("reviews") == post.get("reviews") == review_report,
            "promotion_preserved": pre.get("promotions") == post.get("promotions") == promotion_report,
            "formal_ref_preserved": pre.get("formal_knowledge_refs") == post.get("formal_knowledge_refs"),
            "batch_history_preserved": (
                pre.get("batches") == post.get("batches")
                and pre.get("batch_count") == post.get("batch_count")
                and pre.get("item_count") == post.get("item_count")
            ),
            "critical_identity_exact": (
                diff.get("status") == "PASS"
                and diff.get("critical_diff_count") == 0
                and not diff.get("diff")
                and gate.get("critical_diff_count") == 0
            ),
            "all_states_covered": all(states.get(key) is True for key in STATE_KEYS),
            "provider_calls_zero": (
                gate.get("provider_calls_during_patch") == 0
                and all(providers.get(key) == 0 for key in (
                    "startup_provider_calls", "migration_provider_calls", "patch_provider_calls"
                ))
            ),
            "pre_patch_backup_verified": (
                backup.get("backup_state") == "PUBLISHED" and backup.get("verify_status") == "PASS"
            ),
            "isolated_restore_verified": (
                restore.get("status") == "PASS"
                and restore.get("primary_root_touched") is False
                and restore.get("critical_diff_count") == 0
            ),
            "startup_idempotent_no_fallback": (
                startup.get("no_second_migration") is True
                and startup.get("no_empty_db_fallback") is True
                and startup.get("first_startup", {}).get("ready") is True
                and startup.get("second_startup", {}).get("ready") is True
                and startup.get("first_startup", {}).get("migration_id") is None
                and startup.get("second_startup", {}).get("migration_id") is None
            ),
            "F3_not_applicable_without_migration": faults.get("F3", {}).get("status") == "PASS_NOT_APPLICABLE",
        }
        fault_status = {key: faults.get(key, {}).get("status") for key in FAULT_KEYS}
        checks["fault_smoke"] = all(fault_status.get(key) == "PASS" for key in FAULT_KEYS)
        checks["C2_recovery_regression"] = faults.get("F6_F8", {}).get("status") == "PASS"
        failed = [key for key, passed in checks.items() if not passed]
        errors.extend(failed)
        report = {
            "status": "PASS" if not failed else "BLOCKED",
            "platform": name,
            "evidence_directory": str(evidence_dir),
            "package_type": "ZIP",
            "installer_type": "NONE",
            "test_package_not_release": True,
            "old_package": expected_packages["old"],
            "new_package": expected_packages["new"],
            "installation_id": post.get("installation_id"),
            "persistent_data_root": post.get("persistent_data_root"),
            "state_coverage": states,
            "critical_diff_count": diff.get("critical_diff_count"),
            "provider_calls": gate.get("provider_calls_during_patch"),
            "backup_restore": "PASS" if checks["pre_patch_backup_verified"] and checks["isolated_restore_verified"] else "BLOCKED",
            "fault_status": fault_status,
            "checks": checks,
            "errors": errors,
        }
        return report
    except (OSError, KeyError, TypeError, ValueError, GateError) as error:
        return {
            "status": "BLOCKED",
            "platform": name,
            "evidence_directory": str(evidence_dir),
            "errors": [str(error)],
        }


def summarize(
    windows_evidence: Path,
    macos_evidence: Path,
    package_manifest_path: Path,
    output_path: Path,
    *,
    package_job_result: str = "success",
    native_job_result: str = "success",
    expected_old_source: str = OLD_SOURCE,
    expected_new_source: str = NEW_SOURCE,
) -> dict[str, Any]:
    global_errors: list[str] = []
    try:
        package_manifest = _json_read(package_manifest_path)
        if package_manifest.get("task") != TASK:
            raise GateError("PACKAGE_TASK_MISMATCH")
        if (
            package_manifest.get("package_type") != "ZIP"
            or package_manifest.get("installer_type") != "NONE"
            or package_manifest.get("test_package_not_release") is not True
        ):
            raise GateError("PACKAGE_ACCEPTANCE_SCOPE_MISMATCH")
        if (
            package_manifest.get("old_source_commit") != expected_old_source
            or package_manifest.get("new_source_commit") != expected_new_source
        ):
            raise GateError("PACKAGE_SOURCE_COMMITS_MISMATCH")
    except (OSError, ValueError, GateError) as error:
        package_manifest = {"packages": {}}
        global_errors.append(str(error))

    windows = _platform_result(
        "windows",
        windows_evidence,
        package_manifest,
        expected_old_source=expected_old_source,
        expected_new_source=expected_new_source,
    )
    macos = _platform_result(
        "macos",
        macos_evidence,
        package_manifest,
        expected_old_source=expected_old_source,
        expected_new_source=expected_new_source,
    )
    if package_job_result != "success":
        global_errors.append(f"PACKAGE_JOB_{package_job_result.upper()}")
    if native_job_result != "success":
        global_errors.append(f"NATIVE_JOBS_{native_job_result.upper()}")
    if windows.get("status") == "PASS" and macos.get("status") == "PASS":
        if windows.get("installation_id") == macos.get("installation_id"):
            global_errors.append("PLATFORM_INSTALLATION_IDS_NOT_INDEPENDENT")
        if windows.get("persistent_data_root") == macos.get("persistent_data_root"):
            global_errors.append("PLATFORM_DATA_ROOTS_NOT_INDEPENDENT")
    else:
        global_errors.append("BOTH_NATIVE_PLATFORM_GATES_REQUIRED")
    c2_pass = (
        windows.get("checks", {}).get("C2_recovery_regression") is True
        and macos.get("checks", {}).get("C2_recovery_regression") is True
    )
    status = "PASS" if not global_errors and windows.get("status") == macos.get("status") == "PASS" else "BLOCKED"
    summary = {
        "contract_version": "hardware-native-patch-durability-summary/v1",
        "task": TASK,
        "result": status,
        "old_source": expected_old_source,
        "new_source": expected_new_source,
        "package_type": "ZIP",
        "installer_type": "NONE",
        "test_package_not_release": True,
        "installer_certification": "NOT_RUN",
        "formal_release_package_certification": "NOT_RUN",
        "platforms": {"windows": windows, "macos": macos},
        "windows_native_package_gate": windows.get("status", "BLOCKED"),
        "macos_native_package_gate": macos.get("status", "BLOCKED"),
        "windows_s1_s7": "PASS" if windows.get("checks", {}).get("all_states_covered") else "BLOCKED",
        "macos_s1_s7": "PASS" if macos.get("checks", {}).get("all_states_covered") else "BLOCKED",
        "windows_old_package": windows.get("old_package", {}).get("package_id"),
        "windows_old_sha256": windows.get("old_package", {}).get("sha256"),
        "windows_new_package": windows.get("new_package", {}).get("package_id"),
        "windows_new_sha256": windows.get("new_package", {}).get("sha256"),
        "macos_old_package": macos.get("old_package", {}).get("package_id"),
        "macos_old_sha256": macos.get("old_package", {}).get("sha256"),
        "macos_new_package": macos.get("new_package", {}).get("package_id"),
        "macos_new_sha256": macos.get("new_package", {}).get("sha256"),
        "windows_critical_diff_count": windows.get("critical_diff_count"),
        "macos_critical_diff_count": macos.get("critical_diff_count"),
        "windows_provider_calls": windows.get("provider_calls"),
        "macos_provider_calls": macos.get("provider_calls"),
        "windows_backup_restore": windows.get("backup_restore", "BLOCKED"),
        "macos_backup_restore": macos.get("backup_restore", "BLOCKED"),
        "windows_fault_smoke": "PASS" if windows.get("checks", {}).get("fault_smoke") else "BLOCKED",
        "macos_fault_smoke": "PASS" if macos.get("checks", {}).get("fault_smoke") else "BLOCKED",
        "c2_recovery_regression": "PASS" if c2_pass else "BLOCKED",
        "native_patch_durability_gate": status,
        "ready_for_release_package_binding": status == "PASS",
        "open_blocker": global_errors,
    }
    _json_write(output_path, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build-packages")
    build_parser.add_argument("--output-dir", required=True)
    build_parser.add_argument("--old-source-commit", default=OLD_SOURCE)
    build_parser.add_argument("--new-source-commit", default=NEW_SOURCE)
    verify_parser = subparsers.add_parser("verify-packages")
    verify_parser.add_argument("--manifest", required=True)
    verify_parser.add_argument("--package-dir", required=True)
    verify_parser.add_argument("--old-source-commit", default=OLD_SOURCE)
    verify_parser.add_argument("--new-source-commit", default=NEW_SOURCE)
    summary_parser = subparsers.add_parser("summarize")
    summary_parser.add_argument("--windows-evidence", required=True)
    summary_parser.add_argument("--macos-evidence", required=True)
    summary_parser.add_argument("--package-manifest", required=True)
    summary_parser.add_argument("--output", required=True)
    summary_parser.add_argument("--package-job-result", default="success")
    summary_parser.add_argument("--native-job-result", default="success")
    summary_parser.add_argument("--old-source-commit", default=OLD_SOURCE)
    summary_parser.add_argument("--new-source-commit", default=NEW_SOURCE)
    args = parser.parse_args(argv)

    try:
        if args.command == "build-packages":
            result = build_packages(
                Path(args.output_dir).expanduser().resolve(),
                old_source=args.old_source_commit,
                new_source=args.new_source_commit,
            )
            print("PACKAGE_BUILD=PASS")
            for role in ("old", "new"):
                item = result["packages"][role]
                print(f"{role.upper()}_PACKAGE={item['package_id']}")
                print(f"{role.upper()}_SHA256={item['sha256']}")
            return 0
        if args.command == "verify-packages":
            manifest = verify_package_manifest(
                Path(args.manifest).expanduser().resolve(),
                Path(args.package_dir).expanduser().resolve(),
                expected_old_source=args.old_source_commit,
                expected_new_source=args.new_source_commit,
            )
            print("PACKAGE_BINDING=PASS")
            print("OLD_SOURCE=" + manifest["old_source_commit"])
            print("NEW_SOURCE=" + manifest["new_source_commit"])
            return 0
        summary = summarize(
            Path(args.windows_evidence).expanduser().resolve(),
            Path(args.macos_evidence).expanduser().resolve(),
            Path(args.package_manifest).expanduser().resolve(),
            Path(args.output).expanduser().resolve(),
            package_job_result=args.package_job_result,
            native_job_result=args.native_job_result,
            expected_old_source=args.old_source_commit,
            expected_new_source=args.new_source_commit,
        )
        print("NATIVE_PATCH_DURABILITY_GATE=" + summary["native_patch_durability_gate"])
        return 0 if summary["result"] == "PASS" else 2
    except (OSError, subprocess.CalledProcessError, GateError, ValueError) as error:
        print("RESULT=BLOCKED")
        print("BLOCKER=" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
