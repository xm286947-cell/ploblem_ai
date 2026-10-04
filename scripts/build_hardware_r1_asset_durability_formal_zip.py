#!/usr/bin/env python3
"""Build and bind the scoped Hardware R1 Asset Durability Formal ZIP.

The application payload is assembled from an exact source commit using the
Hardware Case runtime allowlist. Existing Product Test / MVP PREP archives are
never used as release inputs. D2 evidence is embedded as evidence, not as an
application package. The detached final Release Manifest binds the completed
ZIP SHA-256 to native startup reports and the frozen D2 run.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


TASK = "HARDWARE-R1-ASSET-DURABILITY-FORMAL-ZIP-BINDING-001"
SOURCE_BASE = "0d0160cc24f5640642094eb82e2a5fe0d6033891"
PACKAGE_ID_PREFIX = "HARDWARE_R1_ASSET_DURABILITY_FORMAL_"
D2_RUN_ID = 37194611177
D2_HEAD = "ce04ec824e1682891e6266e628e48ecbc4995c2e"
D2_OLD_SOURCE = "4a9cfdfc2c10366c86ab01efcc1c236da5942d38"
D2_NEW_SOURCE = "87fcfaede66f4565eb3fba12330f7b8fba51187a"
D2_TEST_PACKAGE_IDS = {
    "old": "HARDWARE_R1_NATIVE_PATCH_DURABILITY_OLD_4a9cfdfc2c10.zip",
    "new": "HARDWARE_R1_NATIVE_PATCH_DURABILITY_NEW_87fcfaede66f.zip",
}
D2_ARTIFACT_NAMES = (
    "hardware-native-durability-windows",
    "hardware-native-durability-macos",
    "hardware-native-durability-summary",
    "hardware-native-durability-packages",
)
D2_ARTIFACT_IDS = {
    "hardware-native-durability-windows": 11300725500,
    "hardware-native-durability-packages": 11300288239,
    "hardware-native-durability-macos": 11300273472,
    "hardware-native-durability-summary": 11300034094,
}
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
D2_REPORT_FILES = (
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
    "state_coverage.json",
)
TEST_ONLY_FILES = {
    "00_README_FIRST.txt",
    "requirements.txt",
    "requirements-runtime-p0-test.txt",
    "START_HARDWARE_CASE.bat",
    "START_HARDWARE_CASE.sh",
    "START_HARDWARE_CASE.command",
    "run_hardware_case_product_test.bat",
    "run_hardware_case_product_test.sh",
    "run_hardware_case_mvp_smoke.bat",
    "run_hardware_case_mvp_smoke.sh",
    "docs/product/HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1.md",
    "scripts/hardware_case_mvp_smoke.py",
    "scripts/hardware_case_product_test_smoke.py",
    "scripts/hardware_case_fresh_extract_gate.py",
}
RELEASE_CLOSURE_ROOTS = (
    "scripts/hardware_case_web_start.py",
    "scripts/hardware_case_precheck.py",
    "quality_knowledge/web/p0_app.py",
    "services/hardware_case_runtime_adapter.py",
)
REPO_ROOT = Path(__file__).resolve().parents[1]


class GateError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GateError(f"JSON_READ_FAILED:{path}") from error
    if not isinstance(value, dict):
        raise GateError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _write_json(path: Path, value: Any) -> None:
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


def _tree_digest(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: str(item["path"])):
        digest.update(str(entry["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _inventory(root: Path, *, omit: set[str] | None = None) -> list[dict[str, Any]]:
    omitted = omit or set()
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in omitted:
            continue
        files.append({"path": relative, "sha256": _sha256(path), "size_bytes": path.stat().st_size})
    return files


def _resolve_commit(repo_root: Path, commit: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", f"{commit}^{{commit}}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise GateError(f"SOURCE_COMMIT_UNAVAILABLE:{commit}") from error
    resolved = result.stdout.strip()
    if resolved != commit:
        raise GateError(f"SOURCE_COMMIT_MISMATCH:{commit}:{resolved}")
    return resolved


def _formal_package_id(source_commit: str) -> str:
    if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise GateError("SOURCE_COMMIT_MUST_BE_FULL_SHA")
    return f"{PACKAGE_ID_PREFIX}{source_commit[:12]}.zip"


def _extract_source_archive(repo_root: Path, commit: str, destination: Path) -> Path:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    archive_path = destination.parent / f"{destination.name}.tar"
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "archive", "--format=tar", f"--output={archive_path}", commit],
            check=True,
            capture_output=True,
            text=True,
        )
        with tarfile.open(archive_path, "r:") as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                parts = tuple(part for part in relative.parts if part not in {"", "."})
                if relative.is_absolute() or ".." in parts:
                    raise GateError(f"SOURCE_ARCHIVE_PATH_INVALID:{member.name}")
                if member.issym() or member.islnk() or member.isdev():
                    raise GateError(f"SOURCE_ARCHIVE_MEMBER_UNSUPPORTED:{member.name}")
                target = destination.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise GateError(f"SOURCE_ARCHIVE_MEMBER_UNSUPPORTED:{member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise GateError(f"SOURCE_ARCHIVE_MEMBER_UNREADABLE:{member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, member.mode & 0o777)
    except (OSError, tarfile.TarError, subprocess.CalledProcessError) as error:
        raise GateError(f"SOURCE_ARCHIVE_FAILED:{commit}") from error
    finally:
        archive_path.unlink(missing_ok=True)
    return destination


def _load_product_builder(source_root: Path, stage: Path):
    builder_path = source_root / "scripts" / "build_hardware_case_product_test_package.py"
    spec = importlib.util.spec_from_file_location("_hardware_case_product_payload_builder", builder_path)
    if spec is None or spec.loader is None:
        raise GateError("PRODUCT_PAYLOAD_BUILDER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = source_root
    module.DIST = stage.parent
    module.STAGE = stage
    module.CLOSURE_ROOTS = list(RELEASE_CLOSURE_ROOTS)
    return module


def _merge_runtime_requirements(source_root: Path, stage: Path) -> None:
    lines_by_name: dict[str, str] = {}
    for relative in ("requirements.txt", "requirements-runtime-p0-test.txt"):
        path = source_root / relative
        if not path.is_file():
            raise GateError(f"RUNTIME_REQUIREMENTS_MISSING:{relative}")
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            match = re.match(r"([A-Za-z0-9_.-]+)", line)
            if not match:
                raise GateError(f"RUNTIME_REQUIREMENT_UNPARSEABLE:{line}")
            name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            if name in {"pytest", "pytest-cov", "pytest-xdist"}:
                continue
            lines_by_name.setdefault(name, line)
    required = {"fastapi", "uvicorn", "pydantic", "langgraph", "httpx2", "jsonschema", "pyyaml"}
    missing = sorted(required - set(lines_by_name))
    if missing:
        raise GateError("RUNTIME_REQUIREMENTS_INCOMPLETE:" + ",".join(missing))
    target = stage / "requirements-hardware-r1.txt"
    target.write_text("\n".join(lines_by_name[name] for name in sorted(lines_by_name)) + "\n", encoding="utf-8")


def _assemble_runtime_payload(source_root: Path, stage: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    stage = stage.resolve()
    builder = _load_product_builder(source_root, stage)
    stage.mkdir(parents=True, exist_ok=False)
    for relative in builder.INCLUDE_DIRS:
        source = source_root / relative
        if not source.is_dir():
            raise GateError(f"RUNTIME_SOURCE_DIRECTORY_MISSING:{relative}")
        builder.copy_tree(source, stage / relative)
    for pattern in builder.INCLUDE_GLOBS:
        builder.copy_glob(pattern)
    for relative in builder.INCLUDE_FILES:
        if relative in TEST_ONLY_FILES:
            continue
        builder.copy_relative(relative)

    closure = builder.copy_dependency_closure()
    if closure.get("status") != "PASS":
        raise GateError("RUNTIME_IMPORT_CLOSURE_FAILED")
    staged_closure = builder.verify_staged_dependency_closure(list(closure["files"]))
    if staged_closure.get("status") != "PASS":
        raise GateError("STAGED_RUNTIME_IMPORT_CLOSURE_FAILED")
    _write_json(stage / "PACKAGE_DEPENDENCY_CLOSURE.json", {
        "contract_version": "hardware-r1-package-dependency-closure/v1",
        "status": "PASS",
        "source_closure": closure,
        "staged_closure": staged_closure,
    })
    for relative in (
        "data/input/word",
        "data/tree",
        "data/output",
        "data/runtime",
        "data/evidence_sources",
        "data/hardware_case_sources",
        "data/r1_field_validation/input_word",
        "data/r1_field_validation/output_snapshot",
    ):
        (stage / relative).mkdir(parents=True, exist_ok=True)

    _merge_runtime_requirements(source_root, stage)
    builder.security_assertions(builder.inventory())
    return {
        "files": _inventory(stage),
        "file_count": len(_inventory(stage)),
        "dependency_closure": {
            "status": closure.get("status"),
            "file_count": len(closure.get("files") or []),
            "roots": list(RELEASE_CLOSURE_ROOTS),
            "asset_migration_modules": closure.get("asset_migration_modules") or [],
        },
    }


def _check_d2_binding(binding_path: Path, evidence_root: Path) -> dict[str, Any]:
    binding = _read_json(binding_path)
    if binding.get("run_id") != D2_RUN_ID or binding.get("head_sha") != D2_HEAD:
        raise GateError("D2_RUN_BINDING_MISMATCH")
    if binding.get("conclusion") != "success":
        raise GateError("D2_RUN_NOT_SUCCESS")
    artifacts = binding.get("artifacts")
    if not isinstance(artifacts, dict) or any(name not in artifacts for name in D2_ARTIFACT_NAMES):
        raise GateError("D2_EVIDENCE_ARTIFACT_BINDING_INCOMPLETE")
    for name in D2_ARTIFACT_NAMES:
        item = artifacts[name]
        if not isinstance(item, dict) or item.get("expired") is not False or not item.get("id"):
            raise GateError(f"D2_EVIDENCE_ARTIFACT_UNAVAILABLE:{name}")
        if str(item["id"]) != str(D2_ARTIFACT_IDS[name]):
            raise GateError(f"D2_EVIDENCE_ARTIFACT_ID_MISMATCH:{name}")

    summary = _read_json(evidence_root / "summary" / "native_gate_summary.json")
    if summary.get("task") != "HARDWARE-R1-NATIVE-PATCH-DURABILITY-GATE-001":
        raise GateError("D2_SUMMARY_TASK_MISMATCH")
    if summary.get("result") != "PASS" or summary.get("native_patch_durability_gate") != "PASS":
        raise GateError("D2_NATIVE_GATE_NOT_PASS")
    if summary.get("old_source") != D2_OLD_SOURCE or summary.get("new_source") != D2_NEW_SOURCE:
        raise GateError("D2_SOURCE_COMMITS_MISMATCH")
    if summary.get("c2_recovery_regression") != "PASS":
        raise GateError("D2_C2_RECOVERY_REGRESSION_NOT_PASS")

    per_platform: dict[str, dict[str, Any]] = {}
    for platform in ("windows", "macos"):
        item = summary.get("platforms", {}).get(platform)
        if not isinstance(item, dict) or item.get("status") != "PASS":
            raise GateError(f"D2_PLATFORM_NOT_PASS:{platform}")
        if item.get("critical_diff_count") != 0 or item.get("provider_calls") != 0:
            raise GateError(f"D2_PLATFORM_DURABILITY_MISMATCH:{platform}")
        if item.get("backup_restore") != "PASS" or summary.get(f"{platform}_fault_smoke") != "PASS":
            raise GateError(f"D2_PLATFORM_BACKUP_OR_FAULT_NOT_PASS:{platform}")
        if not all((item.get("state_coverage") or {}).get(key) is True for key in STATE_KEYS):
            raise GateError(f"D2_PLATFORM_STATE_COVERAGE_INCOMPLETE:{platform}")
        required_checks = (
            "C2_recovery_regression",
            "all_states_covered",
            "batch_history_preserved",
            "candidate_hash_report_matches",
            "candidate_identity_preserved",
            "critical_identity_exact",
            "data_root_preserved",
            "fault_smoke",
            "formal_ref_preserved",
            "installation_id_preserved",
            "isolated_restore_verified",
            "packages_bound",
            "pre_patch_backup_verified",
            "promotion_preserved",
            "provider_calls_zero",
            "review_preserved",
            "source_commits_bound",
            "source_hash_report_matches",
            "source_identity_preserved",
            "startup_idempotent_no_fallback",
        )
        checks = item.get("checks") or {}
        if not all(checks.get(key) is True for key in required_checks):
            raise GateError(f"D2_PLATFORM_SUMMARY_CHECKS_INCOMPLETE:{platform}")
        faults = item.get("fault_status") or {}
        if any(faults.get(key) != "PASS" for key in FAULT_KEYS):
            raise GateError(f"D2_PLATFORM_FAULT_STATUS_MISMATCH:{platform}")
        if "F3" in faults and faults.get("F3") != "PASS_NOT_APPLICABLE":
            raise GateError(f"D2_PLATFORM_F3_STATUS_MISMATCH:{platform}")

        platform_dir = evidence_root / platform
        for filename in D2_REPORT_FILES:
            if not (platform_dir / filename).is_file():
                raise GateError(f"D2_PLATFORM_REPORT_MISSING:{platform}:{filename}")
        gate = _read_json(platform_dir / "gate_manifest.json")
        diff = _read_json(platform_dir / "identity_diff.json")
        provider = _read_json(platform_dir / "provider_call_report.json")
        backup = _read_json(platform_dir / "backup_verify.json")
        restore = _read_json(platform_dir / "restore_verify.json")
        states = _read_json(platform_dir / "state_coverage.json")
        fault_report = _read_json(platform_dir / "recovery_fault_report.json")
        if gate.get("status") != "PASS" or gate.get("critical_diff_count") != 0:
            raise GateError(f"D2_PLATFORM_GATE_REPORT_MISMATCH:{platform}")
        if diff.get("status") != "PASS" or diff.get("critical_diff_count") != 0 or diff.get("diff") != []:
            raise GateError(f"D2_PLATFORM_IDENTITY_DIFF_MISMATCH:{platform}")
        if any(provider.get(key) != 0 for key in ("startup_provider_calls", "migration_provider_calls", "patch_provider_calls")):
            raise GateError(f"D2_PLATFORM_PROVIDER_CALLS_NONZERO:{platform}")
        if backup.get("verify_status") != "PASS" or restore.get("status") != "PASS" or restore.get("critical_diff_count") != 0:
            raise GateError(f"D2_PLATFORM_BACKUP_RESTORE_MISMATCH:{platform}")
        if not all(states.get(key) is True for key in STATE_KEYS):
            raise GateError(f"D2_PLATFORM_STATE_REPORT_MISMATCH:{platform}")
        if any((fault_report.get(key) or {}).get("status") != "PASS" for key in FAULT_KEYS):
            raise GateError(f"D2_PLATFORM_FAULT_REPORT_MISMATCH:{platform}")
        if (fault_report.get("F3") or {}).get("status") != "PASS_NOT_APPLICABLE":
            raise GateError(f"D2_PLATFORM_F3_REPORT_MISMATCH:{platform}")
        per_platform[platform] = item

    packages_dir = evidence_root / "packages"
    package_manifest = _read_json(packages_dir / "package_manifest.json")
    if package_manifest.get("old_source_commit") != D2_OLD_SOURCE or package_manifest.get("new_source_commit") != D2_NEW_SOURCE:
        raise GateError("D2_PACKAGE_MANIFEST_SOURCE_MISMATCH")
    if package_manifest.get("installer_certification") != "NOT_RUN" or package_manifest.get("formal_release_package_certification") != "NOT_RUN":
        raise GateError("D2_PACKAGE_CERTIFICATION_SCOPE_MISMATCH")
    if package_manifest.get("test_package_not_release") is not True or package_manifest.get("package_type") != "ZIP":
        raise GateError("D2_TEST_PACKAGE_STATUS_MISMATCH")
    for role, commit in (("old", D2_OLD_SOURCE), ("new", D2_NEW_SOURCE)):
        entry = package_manifest.get("packages", {}).get(role)
        if (
            not isinstance(entry, dict)
            or entry.get("source_commit") != commit
            or entry.get("package_id") != D2_TEST_PACKAGE_IDS[role]
        ):
            raise GateError(f"D2_PACKAGE_SOURCE_BINDING_MISMATCH:{role}")
        package_path = packages_dir / str(entry.get("package_id") or "")
        if not package_path.is_file() or _sha256(package_path) != entry.get("sha256"):
            raise GateError(f"D2_TEST_PACKAGE_HASH_INVALID:{role}")
        if package_path.stat().st_size != entry.get("size_bytes"):
            raise GateError(f"D2_TEST_PACKAGE_SIZE_INVALID:{role}")

    return {
        "binding": binding,
        "summary": summary,
        "package_manifest": package_manifest,
        "platforms": per_platform,
    }


def _copy_d2_evidence(source_root: Path, target_root: Path) -> dict[str, Any]:
    target_root.mkdir(parents=True, exist_ok=False)
    copied = []
    for platform in ("windows", "macos"):
        source_dir = source_root / platform
        target_dir = target_root / platform
        target_dir.mkdir(parents=True)
        for source in sorted(source_dir.glob("*.json")):
            shutil.copy2(source, target_dir / source.name)
            copied.append({"path": f"{platform}/{source.name}", "sha256": _sha256(target_dir / source.name), "size_bytes": (target_dir / source.name).stat().st_size})
    summary_source = source_root / "summary" / "native_gate_summary.json"
    shutil.copy2(summary_source, target_root / "native_gate_summary.json")
    copied.append({"path": "native_gate_summary.json", "sha256": _sha256(target_root / "native_gate_summary.json"), "size_bytes": summary_source.stat().st_size})
    package_manifest_source = source_root / "packages" / "package_manifest.json"
    shutil.copy2(package_manifest_source, target_root / "d2_test_package_manifest.json")
    copied.append({"path": "d2_test_package_manifest.json", "sha256": _sha256(target_root / "d2_test_package_manifest.json"), "size_bytes": package_manifest_source.stat().st_size})
    return {"files": copied, "file_count": len(copied), "tree_sha256": _tree_digest(copied)}


def _write_release_launchers(stage: Path) -> None:
    (stage / "START_HARDWARE_R1_ASSET_DURABILITY.bat").write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "call CHECK_ENV.bat web\r\n"
        "if errorlevel 1 exit /b 2\r\n"
        "where python >nul 2>nul\r\n"
        "if not defined HARDWARE_R1_PORT set HARDWARE_R1_PORT=8080\r\n"
        "if %errorlevel%==0 (\r\n"
        "  python scripts\\hardware_case_web_start.py --host 127.0.0.1 --port %HARDWARE_R1_PORT%\r\n"
        ") else (\r\n"
        "  py -3.11 scripts\\hardware_case_web_start.py --host 127.0.0.1 --port %HARDWARE_R1_PORT%\r\n"
        ")\r\n"
        "exit /b %errorlevel%\r\n",
        encoding="ascii",
    )
    shell = stage / "START_HARDWARE_R1_ASSET_DURABILITY.sh"
    shell.write_text(
        "#!/usr/bin/env sh\n"
        "set -eu\n"
        "cd \"$(dirname \"$0\")\"\n"
        "port=\"${HARDWARE_R1_PORT:-8080}\"\n"
        "python3 scripts/hardware_case_precheck.py --mode web\n"
        "exec python3 scripts/hardware_case_web_start.py --host 127.0.0.1 --port \"$port\"\n",
        encoding="utf-8",
    )
    shell.chmod(shell.stat().st_mode | 0o111)
    command = stage / "START_HARDWARE_R1_ASSET_DURABILITY.command"
    command.write_text(
        "#!/usr/bin/env sh\n"
        "set -eu\n"
        "cd \"$(dirname \"$0\")\"\n"
        "exec ./START_HARDWARE_R1_ASSET_DURABILITY.sh\n",
        encoding="utf-8",
    )
    command.chmod(command.stat().st_mode | 0o111)


def _write_readme(stage: Path, package_id: str, source_commit: str, d2_run_id: int) -> None:
    content = f"""HARDWARE R1 ASSET DURABILITY FORMAL ZIP
=======================================

Package: {package_id}
Source commit: {source_commit}
Certification scope: HARDWARE_R1_ASSET_DURABILITY
D2 native durability evidence: GitHub Actions run {d2_run_id}

This package certifies only the Hardware R1 asset-durability scope: Source
Store, Persistent Data Root, Candidate Asset, Production Review, Promotion
Asset Ledger, Publication Recovery, backup/restore, startup/recovery and
preservation of source/candidate/review/promotion/formal-reference identity
across an application-package patch.

This is a scoped Hardware R1 release ZIP, not a release declaration for the
entire Hardware Case MVP. Tree product gates, the 20-30 real-case integration
gate, the full frontend product gate, and MSI/DMG/PKG installer certification
are outside this certification.

First use:
1. Extract this ZIP into a clean directory.
2. Install Python 3.11 or newer.
3. Install dependencies: python -m pip install -r requirements-hardware-r1.txt
4. Windows: run START_HARDWARE_R1_ASSET_DURABILITY.bat
   macOS: run ./START_HARDWARE_R1_ASSET_DURABILITY.sh
5. Open http://127.0.0.1:8080/p0/hardware-cases

Persistent data is stored outside the application package using the platform's
HardwareKnowledge data root. Do not replace or delete that data root when
patching the application package. D2 per-platform reports are embedded under
D2_EVIDENCE/. The detached RELEASE_MANIFEST.json delivered alongside this ZIP
binds its SHA-256 to native startup reports and the D2 evidence.

Non-claims: Hardware Case MVP release is NOT_CERTIFIED; no product-wide release
claim is made; installer certification was NOT_RUN. Existing Product Test and
MVP PREP archives were not relabeled or reused as this release ZIP.
"""
    (stage / "RELEASE_README.txt").write_text(content, encoding="utf-8")


def _zip_tree(root: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2020, 1, 1, 0, 0, 0))
            mode = path.stat().st_mode & 0o777
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build_package(
    output_dir: Path,
    source_commit: str,
    d2_evidence_root: Path,
    d2_binding_path: Path,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    package_id = _formal_package_id(source_commit)
    _resolve_commit(repo_root, source_commit)
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise GateError("RELEASE_OUTPUT_DIR_MUST_BE_EMPTY_OR_NONEXISTENT")
    output_dir.mkdir(parents=True, exist_ok=True)
    d2 = _check_d2_binding(d2_binding_path, d2_evidence_root)
    with tempfile.TemporaryDirectory(prefix="hardware-r1-formal-zip-") as temp_name:
        temp_root = Path(temp_name)
        release_source = _extract_source_archive(repo_root, source_commit, temp_root / "source-release")
        d2_source = _extract_source_archive(repo_root, D2_NEW_SOURCE, temp_root / "source-d2-tested")
        release_payload = temp_root / "release-runtime"
        d2_payload = temp_root / "d2-runtime"
        release_inventory = _assemble_runtime_payload(release_source, release_payload)
        d2_inventory = _assemble_runtime_payload(d2_source, d2_payload)
        release_digest = _tree_digest(release_inventory["files"])
        d2_digest = _tree_digest(d2_inventory["files"])
        if release_digest != d2_digest or release_inventory["files"] != d2_inventory["files"]:
            raise GateError("D2_TESTED_RELEASE_RUNTIME_PAYLOAD_MISMATCH")

        stage = temp_root / "formal-release-stage"
        shutil.copytree(release_payload, stage)
        _write_release_launchers(stage)
        _write_readme(stage, package_id, source_commit, D2_RUN_ID)
        d2_output = stage / "D2_EVIDENCE"
        evidence_manifest = _copy_d2_evidence(d2_evidence_root, d2_output)
        d2_binding_record = {
            "run_id": D2_RUN_ID,
            "head_sha": D2_HEAD,
            "run_url": d2.get("binding", {}).get("run_url"),
            "artifact_ids": {name: d2["binding"]["artifacts"][name]["id"] for name in D2_ARTIFACT_NAMES},
            "artifact_metadata": {name: d2["binding"]["artifacts"][name] for name in D2_ARTIFACT_NAMES},
            "new_source_commit_tested": D2_NEW_SOURCE,
            "release_source_commit": source_commit,
            "runtime_payload_sha256_equal": True,
            "runtime_payload_sha256": release_digest,
            "scope": "HARDWARE_R1_ASSET_DURABILITY",
        }
        _write_json(
            d2_output / "binding.json",
            d2_binding_record,
        )
        evidence_manifest["files"].append(
            {
                "path": "binding.json",
                "sha256": _sha256(d2_output / "binding.json"),
                "size_bytes": (d2_output / "binding.json").stat().st_size,
            }
        )
        evidence_manifest["files"].sort(key=lambda item: item["path"])
        evidence_manifest["file_count"] = len(evidence_manifest["files"])
        evidence_manifest["tree_sha256"] = _tree_digest(evidence_manifest["files"])
        content_manifest = {
            "contract_version": "hardware-r1-asset-durability-content/v1",
            "task": TASK,
            "CERTIFICATION_STATUS": "PENDING_NATIVE_STARTUP_BINDING",
            "CERTIFICATION_SCOPE": "HARDWARE_R1_ASSET_DURABILITY",
            "package_id": package_id,
            "package_type": "ZIP",
            "source_commit": source_commit,
            "certification_scope": "HARDWARE_R1_ASSET_DURABILITY",
            "payload_status": "PASS",
            "runtime_payload_sha256": release_digest,
            "runtime_payload_files": release_inventory["file_count"],
            "runtime_payload_inventory": release_inventory["files"],
            "d2_tested_runtime_payload_sha256": d2_digest,
            "d2_tested_runtime_payload_matches": True,
            "d2_run_id": D2_RUN_ID,
            "d2_head_sha": D2_HEAD,
            "d2_evidence_tree_sha256": evidence_manifest["tree_sha256"],
            "d2_native_gate_summary_sha256": _sha256(d2_output / "native_gate_summary.json"),
            "d2_artifact_ids": d2_binding_record["artifact_ids"],
            "d2_artifact_metadata": d2_binding_record["artifact_metadata"],
            "d2_evidence_files": evidence_manifest["files"],
            "nonclaims": {
                "hardware_case_mvp_release": "NOT_CERTIFIED",
                "product_release_claim": "NOT_MADE",
                "installer_certification": "NOT_RUN",
                "test_package_not_release": "NOT_REUSED_AS_RELEASE",
            },
        }
        _write_json(stage / "RELEASE_CONTENT_MANIFEST.json", content_manifest)
        archive_path = output_dir / package_id
        _zip_tree(stage, archive_path)
        package_hash = _sha256(archive_path)
        checksum_path = output_dir / f"{package_id}.sha256"
        checksum_path.write_text(f"{package_hash}  {package_id}\n", encoding="ascii")
        build_manifest = {
            "contract_version": "hardware-r1-asset-durability-package-build/v1",
            "task": TASK,
            "CERTIFICATION_STATUS": "PENDING_NATIVE_STARTUP_BINDING",
            "CERTIFICATION_SCOPE": "HARDWARE_R1_ASSET_DURABILITY",
            "package_id": package_id,
            "package_path": archive_path.name,
            "package_sha256": package_hash,
            "package_size_bytes": archive_path.stat().st_size,
            "source_commit": source_commit,
            "certification_scope": "HARDWARE_R1_ASSET_DURABILITY",
            "runtime_payload_sha256": release_digest,
            "d2_tested_runtime_payload_sha256": d2_digest,
            "d2_run_id": D2_RUN_ID,
            "d2_head_sha": D2_HEAD,
            "d2_evidence_tree_sha256": evidence_manifest["tree_sha256"],
            "d2_native_gate_summary_sha256": _sha256(d2_output / "native_gate_summary.json"),
            "d2_artifact_ids": {name: d2["binding"]["artifacts"][name]["id"] for name in D2_ARTIFACT_NAMES},
            "d2_artifact_metadata": {name: d2["binding"]["artifacts"][name] for name in D2_ARTIFACT_NAMES},
            "installer_certification": "NOT_RUN",
            "hardware_case_mvp_release": "NOT_CERTIFIED",
            "product_release_claim": "NOT_MADE",
            "test_package_not_release": "NOT_REUSED_AS_RELEASE",
        }
        _write_json(output_dir / "package_build_manifest.json", build_manifest)
        return build_manifest


def _validate_startup_report(path: Path, platform: str, build: dict[str, Any]) -> dict[str, Any]:
    report = _read_json(path)
    if report.get("status") != "PASS" or report.get("platform") != platform:
        raise GateError(f"NATIVE_STARTUP_REPORT_NOT_PASS:{platform}")
    for key in ("package_id", "package_sha256", "source_commit"):
        if report.get(key) != build.get(key):
            raise GateError(f"NATIVE_STARTUP_PACKAGE_BINDING_MISMATCH:{platform}:{key}")
    if (
        report.get("fresh_extract") is not True
        or report.get("persistent_data_root_isolated") is not True
        or report.get("startup_ready") is not True
        or not report.get("installation_id")
        or not report.get("persistent_data_root")
    ):
        raise GateError(f"NATIVE_STARTUP_ISOLATION_FAILED:{platform}")
    endpoints = report.get("endpoints")
    required = {"/health", "/api/system/hardware/startup", "/p0/hardware-cases"}
    if not isinstance(endpoints, dict) or any(endpoints.get(path) != 200 for path in required):
        raise GateError(f"NATIVE_STARTUP_ENDPOINTS_FAILED:{platform}")
    return report


def finalize_release_manifest(
    package_build_manifest_path: Path,
    d2_binding_path: Path,
    windows_report_path: Path,
    macos_report_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    build = _read_json(package_build_manifest_path)
    source_commit = build.get("source_commit")
    try:
        expected_package_id = _formal_package_id(source_commit)
    except GateError as error:
        raise GateError("RELEASE_BUILD_BINDING_INVALID") from error
    if (
        build.get("task") != TASK
        or build.get("CERTIFICATION_STATUS") != "PENDING_NATIVE_STARTUP_BINDING"
        or build.get("certification_scope") != "HARDWARE_R1_ASSET_DURABILITY"
        or build.get("runtime_payload_sha256") != build.get("d2_tested_runtime_payload_sha256")
        or build.get("package_id") != expected_package_id
    ):
        raise GateError("RELEASE_BUILD_BINDING_INVALID")
    archive_path = package_build_manifest_path.parent / str(build.get("package_id") or "")
    if not archive_path.is_file() or _sha256(archive_path) != build.get("package_sha256"):
        raise GateError("RELEASE_PACKAGE_HASH_MISMATCH")
    if archive_path.stat().st_size != build.get("package_size_bytes"):
        raise GateError("RELEASE_PACKAGE_SIZE_MISMATCH")
    checksum_path = package_build_manifest_path.parent / f"{build['package_id']}.sha256"
    if not checksum_path.is_file() or checksum_path.read_text(encoding="ascii").strip() != f"{build['package_sha256']}  {build['package_id']}":
        raise GateError("RELEASE_PACKAGE_CHECKSUM_SIDECAR_INVALID")
    binding = _read_json(d2_binding_path)
    if binding.get("run_id") != D2_RUN_ID or binding.get("head_sha") != D2_HEAD or binding.get("conclusion") != "success":
        raise GateError("D2_FINAL_BINDING_MISMATCH")
    artifact_ids = {name: binding.get("artifacts", {}).get(name, {}).get("id") for name in D2_ARTIFACT_NAMES}
    if artifact_ids != build.get("d2_artifact_ids"):
        raise GateError("D2_FINAL_ARTIFACT_IDS_MISMATCH")
    artifact_metadata = {name: binding.get("artifacts", {}).get(name) for name in D2_ARTIFACT_NAMES}
    if artifact_metadata != build.get("d2_artifact_metadata"):
        raise GateError("D2_FINAL_ARTIFACT_METADATA_MISMATCH")
    if any(
        not isinstance(binding.get("artifacts", {}).get(name), dict)
        or binding["artifacts"][name].get("expired") is not False
        or str(binding["artifacts"][name].get("id")) != str(D2_ARTIFACT_IDS[name])
        for name in D2_ARTIFACT_NAMES
    ):
        raise GateError("D2_FINAL_ARTIFACT_UNAVAILABLE")

    try:
        with zipfile.ZipFile(archive_path) as archive:
            content_manifest = json.loads(archive.read("RELEASE_CONTENT_MANIFEST.json"))
            embedded_binding = json.loads(archive.read("D2_EVIDENCE/binding.json"))
            for entry in content_manifest.get("runtime_payload_inventory", []):
                relative = PurePosixPath(str(entry.get("path") or ""))
                if relative.is_absolute() or ".." in relative.parts:
                    raise GateError("RELEASE_RUNTIME_INVENTORY_PATH_INVALID")
                payload = archive.read(relative.as_posix())
                if hashlib.sha256(payload).hexdigest() != entry.get("sha256") or len(payload) != entry.get("size_bytes"):
                    raise GateError(f"RELEASE_RUNTIME_PAYLOAD_HASH_MISMATCH:{relative}")
            evidence_files = content_manifest.get("d2_evidence_files", [])
            for entry in evidence_files:
                relative = PurePosixPath(str(entry.get("path") or ""))
                if relative.is_absolute() or ".." in relative.parts:
                    raise GateError("D2_EVIDENCE_INVENTORY_PATH_INVALID")
                payload = archive.read("D2_EVIDENCE/" + relative.as_posix())
                if hashlib.sha256(payload).hexdigest() != entry.get("sha256") or len(payload) != entry.get("size_bytes"):
                    raise GateError(f"D2_EMBEDDED_EVIDENCE_HASH_MISMATCH:{relative}")
    except (KeyError, OSError, ValueError, zipfile.BadZipFile) as error:
        raise GateError("RELEASE_CONTENT_MANIFEST_INVALID") from error
    runtime_inventory = content_manifest.get("runtime_payload_inventory")
    evidence_inventory = content_manifest.get("d2_evidence_files")
    if not isinstance(runtime_inventory, list) or _tree_digest(runtime_inventory) != build.get("runtime_payload_sha256"):
        raise GateError("RELEASE_RUNTIME_INVENTORY_TREE_HASH_MISMATCH")
    if not isinstance(evidence_inventory, list) or _tree_digest(evidence_inventory) != build.get("d2_evidence_tree_sha256"):
        raise GateError("D2_EVIDENCE_INVENTORY_TREE_HASH_MISMATCH")

    for key, expected in (
        ("task", TASK),
        ("CERTIFICATION_STATUS", "PENDING_NATIVE_STARTUP_BINDING"),
        ("CERTIFICATION_SCOPE", "HARDWARE_R1_ASSET_DURABILITY"),
        ("package_id", build.get("package_id")),
        ("source_commit", source_commit),
        ("certification_scope", "HARDWARE_R1_ASSET_DURABILITY"),
        ("runtime_payload_sha256", build.get("runtime_payload_sha256")),
        ("d2_evidence_tree_sha256", build.get("d2_evidence_tree_sha256")),
        ("d2_native_gate_summary_sha256", build.get("d2_native_gate_summary_sha256")),
        ("d2_artifact_ids", build.get("d2_artifact_ids")),
        ("d2_artifact_metadata", build.get("d2_artifact_metadata")),
    ):
        if content_manifest.get(key) != expected:
            raise GateError(f"RELEASE_CONTENT_MANIFEST_BINDING_MISMATCH:{key}")
    nonclaims = content_manifest.get("nonclaims") or {}
    expected_nonclaims = {
        "hardware_case_mvp_release": "NOT_CERTIFIED",
        "product_release_claim": "NOT_MADE",
        "installer_certification": "NOT_RUN",
        "test_package_not_release": "NOT_REUSED_AS_RELEASE",
    }
    if any(nonclaims.get(key) != value for key, value in expected_nonclaims.items()):
        raise GateError("RELEASE_CONTENT_NONCLAIMS_MISMATCH")
    if embedded_binding.get("artifact_ids") != build.get("d2_artifact_ids"):
        raise GateError("D2_EMBEDDED_BINDING_MISMATCH")
    if (
        embedded_binding.get("run_id") != D2_RUN_ID
        or embedded_binding.get("head_sha") != D2_HEAD
        or embedded_binding.get("artifact_metadata") != build.get("d2_artifact_metadata")
        or embedded_binding.get("scope") != "HARDWARE_R1_ASSET_DURABILITY"
    ):
        raise GateError("D2_EMBEDDED_BINDING_INVALID")
    windows = _validate_startup_report(windows_report_path, "windows", build)
    macos = _validate_startup_report(macos_report_path, "macos", build)
    if (
        not windows.get("installation_id")
        or windows.get("installation_id") == macos.get("installation_id")
        or windows.get("persistent_data_root") == macos.get("persistent_data_root")
    ):
        raise GateError("NATIVE_INSTALLATION_OR_DATA_ROOTS_NOT_INDEPENDENT")
    if build.get("runtime_payload_sha256") != build.get("d2_tested_runtime_payload_sha256"):
        raise GateError("D2_TESTED_RUNTIME_PAYLOAD_BINDING_MISMATCH")
    manifest = {
        "contract_version": "hardware-r1-asset-durability-release-binding/v1",
        "task": TASK,
        "CERTIFICATION_STATUS": "PASS",
        "CERTIFICATION_SCOPE": "HARDWARE_R1_ASSET_DURABILITY",
        "HARDWARE_R1_ASSET_DURABILITY_CERTIFICATION": "PASS",
        "SOURCE_COMMIT": build["source_commit"],
        "PACKAGE_ID": build["package_id"],
        "PACKAGE_SHA256": build["package_sha256"],
        "PACKAGE_SIZE_BYTES": build["package_size_bytes"],
        "WINDOWS_NATIVE_STARTUP": "PASS",
        "MACOS_NATIVE_STARTUP": "PASS",
        "PRODUCT_READINESS": "NOT_CERTIFIED_OUT_OF_SCOPE",
        "WINDOWS_PATCH_DURABILITY": "PASS",
        "MACOS_PATCH_DURABILITY": "PASS",
        "CRITICAL_IDENTITY_DIFF_COUNT": 0,
        "PROVIDER_CALLS_DURING_PATCH": 0,
        "D2_EVIDENCE": {
            "run_id": D2_RUN_ID,
            "head_sha": D2_HEAD,
            "run_url": binding.get("run_url"),
            "artifact_ids": {name: binding["artifacts"][name]["id"] for name in D2_ARTIFACT_NAMES},
            "artifact_names": list(D2_ARTIFACT_NAMES),
            "embedded_summary_sha256": build["d2_native_gate_summary_sha256"],
            "embedded_evidence_tree_sha256": build["d2_evidence_tree_sha256"],
            "runtime_payload_sha256": build["runtime_payload_sha256"],
            "tested_new_source_commit": D2_NEW_SOURCE,
            "release_runtime_payload_matches_d2_tested_payload": True,
        },
        "NATIVE_STARTUP_REPORTS": {
            "windows": {"sha256": _sha256(windows_report_path), "installation_id": windows.get("installation_id"), "persistent_data_root": windows.get("persistent_data_root"), "report_file": "windows_startup_report.json"},
            "macos": {"sha256": _sha256(macos_report_path), "installation_id": macos.get("installation_id"), "persistent_data_root": macos.get("persistent_data_root"), "report_file": "macos_startup_report.json"},
        },
        "HARDWARE_CASE_MVP_RELEASE": "NOT_CERTIFIED",
        "FORMAL_HARDWARE_PRODUCT_RELEASE": "NOT_CERTIFIED",
        "PRODUCT_RELEASE_CLAIM": "NOT_MADE",
        "INSTALLER_CERTIFICATION": "NOT_RUN",
        "TEST_PACKAGE_NOT_RELEASE": "NOT_REUSED_AS_RELEASE",
        "OUT_OF_SCOPE": [
            "ENTIRE_HARDWARE_CASE_MVP_RELEASE",
            "TREE_PRODUCT_RELEASE_GATES",
            "20_TO_30_REAL_CASE_MVP_INTEGRATION_GATE",
            "FULL_FRONTEND_PRODUCT_GATE",
            "MSI_DMG_PKG_INSTALLER_CERTIFICATION",
        ],
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(output_path, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build")
    build.add_argument("--source-commit", default=SOURCE_BASE)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--d2-evidence-root", type=Path, required=True)
    build.add_argument("--d2-binding", type=Path, required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--package-build-manifest", type=Path, required=True)
    finalize.add_argument("--d2-binding", type=Path, required=True)
    finalize.add_argument("--windows-report", type=Path, required=True)
    finalize.add_argument("--macos-report", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build_package(args.output_dir, args.source_commit, args.d2_evidence_root, args.d2_binding)
            print(f"PACKAGE_ID={result['package_id']}")
            print(f"PACKAGE_SHA256={result['package_sha256']}")
            print(f"SOURCE_COMMIT={result['source_commit']}")
            print("PACKAGE_BUILD=PASS")
        else:
            result = finalize_release_manifest(
                args.package_build_manifest,
                args.d2_binding,
                args.windows_report,
                args.macos_report,
                args.output,
            )
            print(f"CERTIFICATION_STATUS={result['CERTIFICATION_STATUS']}")
            print(f"PACKAGE_ID={result['PACKAGE_ID']}")
            print(f"PACKAGE_SHA256={result['PACKAGE_SHA256']}")
            print("RELEASE_MANIFEST=PASS")
    except GateError as error:
        print("RESULT=BLOCKED")
        print(f"BLOCKER={error}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
