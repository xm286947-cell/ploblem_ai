#!/usr/bin/env python3
"""Build old-version durable state, replace only the app root, and verify it.

Source trees and ZIP/TAR packages are copied into a disposable sibling
application area. The supplied persistent data root is never copied, moved,
or removed. This source-level harness is the D1 gate, not a native installer
or Windows/macOS patch certification.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_PATH = REPO_ROOT / "tests" / "test_hardware_patch_durability.py"
EXPECTED_OLD_BASE = "4a9cfdfc2c10366c86ab01efcc1c236da5942d38"
REQUIRED_EVIDENCE = (
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
    "README.md",
)
COPY_IGNORES = shutil.ignore_patterns(
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules",
    "dist", "build", "PATCH_DURABILITY_EVIDENCE", ".codex", "*.pyc",
)


class GateError(RuntimeError):
    pass


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _text_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = file_path.relative_to(path).as_posix()
        if any(part in {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"} for part in Path(relative).parts):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(file_path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _git_commit(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _git_dirty(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(result.stdout.strip())


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_extract_zip(package: Path, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            parts = tuple(part for part in member.parts if part not in {"", "."})
            if member.is_absolute() or ".." in parts:
                raise GateError(f"PACKAGE_PATH_INVALID:{info.filename}")
            destination = (target / Path(*parts)).resolve()
            if not _inside(destination, root):
                raise GateError(f"PACKAGE_PATH_INVALID:{info.filename}")
        archive.extractall(target)


def _safe_extract_tar(package: Path, target: Path) -> None:
    root = target.resolve()
    with tarfile.open(package, "r:*") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            parts = tuple(part for part in path.parts if part not in {"", "."})
            if path.is_absolute() or ".." in parts:
                raise GateError(f"PACKAGE_PATH_INVALID:{member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise GateError(f"PACKAGE_MEMBER_TYPE_UNSUPPORTED:{member.name}")
            if not parts and member.isdir():
                continue
            destination = (target / Path(*parts)).resolve()
            if not _inside(destination, root):
                raise GateError(f"PACKAGE_PATH_INVALID:{member.name}")
        archive.extractall(target)


def _locate_application_root(container: Path) -> Path:
    if (container / "services" / "hardware_startup_coordinator.py").is_file():
        return container
    candidates = [
        directory
        for directory in container.iterdir()
        if directory.is_dir()
        and (directory / "services" / "hardware_startup_coordinator.py").is_file()
    ]
    if len(candidates) != 1:
        raise GateError("APPLICATION_ROOT_NOT_UNIQUE")
    return candidates[0]


def _materialize(
    source: Path | None,
    package: Path | None,
    target: Path,
    *,
    package_adapter: str | None,
) -> dict[str, Any]:
    if (source is None) == (package is None):
        raise GateError("SPECIFY_EXACTLY_ONE_SOURCE_OR_PACKAGE_PER_VERSION")
    target.mkdir(parents=True, exist_ok=False)
    if source is not None:
        if not source.is_dir():
            raise GateError(f"SOURCE_NOT_DIRECTORY:{source}")
        copied_source = target / "source"
        shutil.copytree(
            source,
            copied_source,
            dirs_exist_ok=True,
            ignore=COPY_IGNORES,
            symlinks=True,
        )
        if any(path.is_symlink() for path in copied_source.rglob("*")):
            raise GateError("SOURCE_TREE_SYMLINK_UNSUPPORTED")
        app_root = _locate_application_root(target / "source")
        source_hash = _tree_sha256(source)
        return {
            "root": app_root,
            "kind": "SOURCE",
            "id": source.name,
            "sha256": source_hash,
            "commit": _git_commit(source),
        }

    assert package is not None
    if not package.is_file():
        raise GateError(f"PACKAGE_NOT_FOUND:{package}")
    unpacked = target / "package"
    unpacked.mkdir()
    if package_adapter:
        module_name, separator, function_name = package_adapter.partition(":")
        if not separator or not module_name or not function_name:
            raise GateError("PACKAGE_ADAPTER_MUST_BE_MODULE_COLON_FUNCTION")
        try:
            adapter = getattr(importlib.import_module(module_name), function_name)
            materialized = Path(adapter(package, unpacked)).expanduser().resolve()
        except Exception as error:
            raise GateError(f"PACKAGE_ADAPTER_FAILED:{type(error).__name__}") from error
        if not _inside(materialized, unpacked.resolve()):
            raise GateError("PACKAGE_ADAPTER_OUTPUT_OUTSIDE_STAGING_ROOT")
        app_root = _locate_application_root(materialized)
    elif zipfile.is_zipfile(package):
        _safe_extract_zip(package, unpacked)
        app_root = _locate_application_root(unpacked)
    elif tarfile.is_tarfile(package):
        _safe_extract_tar(package, unpacked)
        app_root = _locate_application_root(unpacked)
    else:
        raise GateError("PACKAGE_FORMAT_UNSUPPORTED_USE_ARCHIVE_OR_PACKAGE_ADAPTER")
    return {
        "root": app_root,
        "kind": "PACKAGE",
        "id": package.name,
        "sha256": _sha256_file(package),
        "commit": None,
    }


def _run_phase(
    *,
    phase: str,
    app_root: Path,
    data_root: Path,
    evidence_root: Path,
    timeout: int,
) -> dict[str, Any]:
    env = dict(os.environ)
    env.update(
        {
            "HARDWARE_DURABILITY_PHASE": phase,
            "HARDWARE_DURABILITY_CODE_ROOT": str(app_root),
            "HARDWARE_DURABILITY_ACTIVE_APP_ROOT": str(app_root),
            "HARDWARE_DURABILITY_DATA_ROOT": str(data_root),
            "HARDWARE_DURABILITY_EVIDENCE_ROOT": str(evidence_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        f"{TEST_PATH}::test_patch_durability_phase",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise GateError(f"PYTEST_TIMEOUT:{phase}") from error
    report = {
        "phase": phase,
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if result.returncode != 0:
        _json_write(evidence_root / f"{phase}_failure.json", report)
        raise GateError(f"PHASE_FAILED:{phase}: see {phase}_failure.json")
    return report


def _run_fault_regression(app_root: Path, evidence_root: Path, timeout: int) -> dict[str, Any]:
    env = dict(os.environ)
    env.pop("HARDWARE_DURABILITY_PHASE", None)
    env["HARDWARE_DURABILITY_CODE_ROOT"] = str(app_root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    checks = {
        "F1": [
            "tests/test_hardware_r1_workbench.py::test_candidate_commit_precedes_ready_and_survives_restart_and_cache_clear",
            "tests/test_hardware_r1_workbench.py::test_review_commit_restart_recovers_ready_after_workbench_write_failure",
        ],
        "F2": [
            "tests/test_hardware_patch_durability.py::test_f2_backup_staging_crash_fails_closed",
        ],
        "F4": [
            "tests/test_hardware_patch_durability.py::test_f4_restore_activation_crash_does_not_activate_partial_restore",
        ],
        "F5": [
            "tests/test_hardware_source_crash_safety.py::test_delete_crash_after_quarantine_restores_source_before_logical_commit",
        ],
        "F6_F8": ["tests/test_hardware_r1_knowledge_promotion.py"],
    }
    reports: dict[str, Any] = {}
    for name, nodes in checks.items():
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *nodes]
        try:
            result = subprocess.run(
                command,
                cwd=app_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            reports[name] = {"status": "BLOCKED", "error": "PYTEST_TIMEOUT"}
            break
        reports[name] = {
            "status": "PASS" if result.returncode == 0 else "BLOCKED",
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if result.returncode != 0:
            break
    reports["F3"] = {
        "status": "PASS_NOT_APPLICABLE",
        "reason": "The D1 patch changes no persistent schema; first and second startup schema versions are compared by the gate.",
    }
    reports["F6_F8"] = reports.get("F6_F8", {"status": "BLOCKED", "reason": "not executed"})
    _json_write(evidence_root / "recovery_fault_report.json", reports)
    blocked = [key for key, value in reports.items() if key != "F3" and value.get("status") != "PASS"]
    if blocked:
        raise GateError("FAULT_REGRESSION_FAILED:" + ",".join(blocked))
    return reports


def _validate_preconditions(args: argparse.Namespace) -> tuple[Path, Path, Path | None, Path | None, Path | None, Path | None]:
    old_source = Path(args.old_source).expanduser().resolve() if args.old_source else None
    new_source = Path(args.new_source).expanduser().resolve() if args.new_source else None
    old_package = Path(args.old_package).expanduser().resolve() if args.old_package else None
    new_package = Path(args.new_package).expanduser().resolve() if args.new_package else None
    data_root = Path(args.persistent_data_root).expanduser().resolve()
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    for source, package, version in (
        (old_source, old_package, "OLD"),
        (new_source, new_package, "NEW"),
    ):
        if (source is None) == (package is None):
            raise GateError(f"{version}_SOURCE_OR_PACKAGE_REQUIRED")
    root_anchor = Path(data_root.anchor)
    home_path = Path.home().expanduser().resolve()
    working_path = Path.cwd().resolve()
    if (
        data_root == root_anchor
        or data_root == home_path
        or data_root == working_path
        or _inside(home_path, data_root)
        or _inside(working_path, data_root)
        or _inside(data_root, working_path)
    ):
        raise GateError("PERSISTENT_DATA_ROOT_TARGET_TOO_BROAD")
    if (
        evidence_root == root_anchor
        or evidence_root == home_path
        or evidence_root == working_path
        or _inside(home_path, evidence_root)
        or _inside(working_path, evidence_root)
    ):
        raise GateError("EVIDENCE_ROOT_TARGET_TOO_BROAD")
    if data_root.exists() and (not data_root.is_dir() or any(data_root.iterdir())):
        raise GateError("PERSISTENT_DATA_ROOT_MUST_BE_EMPTY_OR_NONEXISTENT")
    if evidence_root.exists() and (not evidence_root.is_dir() or any(evidence_root.iterdir())):
        raise GateError("EVIDENCE_ROOT_MUST_BE_EMPTY_OR_NONEXISTENT")
    for external in (old_source, new_source, old_package, new_package):
        if external and (_inside(data_root, external) or _inside(external, data_root)):
            raise GateError("PERSISTENT_DATA_ROOT_MUST_BE_SEPARATE_FROM_APPLICATION_INPUTS")
        if external and (_inside(evidence_root, external) or _inside(external, evidence_root)):
            raise GateError("EVIDENCE_ROOT_MUST_BE_SEPARATE_FROM_APPLICATION_INPUTS")
    if _inside(evidence_root, data_root) or _inside(data_root, evidence_root):
        raise GateError("EVIDENCE_ROOT_MUST_BE_SEPARATE_FROM_PERSISTENT_DATA_ROOT")
    restore_root = data_root.parent / (data_root.name + "-restore-drill")
    if restore_root.exists():
        raise GateError("ISOLATED_RESTORE_ROOT_ALREADY_EXISTS")
    return data_root, evidence_root, old_source, new_source, old_package, new_package


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-source", default=os.getenv("OLD_SOURCE"))
    parser.add_argument("--new-source", default=os.getenv("NEW_SOURCE"))
    parser.add_argument("--old-package", default=os.getenv("OLD_PACKAGE"))
    parser.add_argument("--new-package", default=os.getenv("NEW_PACKAGE"))
    parser.add_argument("--package-adapter", default=os.getenv("HARDWARE_PATCH_PACKAGE_ADAPTER"))
    parser.add_argument("--old-source-commit", default=os.getenv("OLD_SOURCE_COMMIT", EXPECTED_OLD_BASE))
    parser.add_argument("--new-source-commit", default=os.getenv("NEW_SOURCE_COMMIT"))
    parser.add_argument("--persistent-data-root", default=os.getenv("PERSISTENT_DATA_ROOT"))
    parser.add_argument(
        "--evidence-root",
        default=os.getenv("EVIDENCE_ROOT", str(Path.cwd() / "PATCH_DURABILITY_EVIDENCE")),
    )
    parser.add_argument("--timeout-seconds", type=int, default=900)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.persistent_data_root:
        print("RESULT=BLOCKED\nBLOCKER=PERSISTENT_DATA_ROOT_REQUIRED")
        return 2
    try:
        data_root, evidence_root, old_source, new_source, old_package, new_package = _validate_preconditions(args)
        evidence_root.mkdir(parents=True, exist_ok=True)
        data_root.parent.mkdir(parents=True, exist_ok=True)
        gate: dict[str, Any] = {
            "contract_version": "hardware-patch-durability-gate/v1",
            "status": "RUNNING",
            "old_source_base": (_git_commit(old_source) or args.old_source_commit) if old_source else args.old_source_commit,
            "new_source_commit": _git_commit(new_source) if new_source else args.new_source_commit,
            "old_input_kind": "SOURCE" if old_source else "PACKAGE",
            "old_input_id": old_source.name if old_source else old_package.name,
            "old_source_tree_sha256": _tree_sha256(old_source) if old_source else None,
            "old_package_sha256": _sha256_file(old_package) if old_package else None,
            "new_input_kind": "SOURCE" if new_source else "PACKAGE",
            "new_input_id": new_source.name if new_source else new_package.name,
            "new_source_tree_sha256": _tree_sha256(new_source) if new_source else None,
            "new_package_sha256": _sha256_file(new_package) if new_package else None,
            "persistent_data_root": str(data_root),
            "native_patch_gate": "NOT_RUN_D1_ONLY",
        }
        _json_write(evidence_root / "gate_manifest.json", gate)

        with tempfile.TemporaryDirectory(prefix="hardware-patch-durability-") as temporary:
            operation_root = Path(temporary)
            old_material = _materialize(
                old_source,
                old_package,
                operation_root / "old-input",
                package_adapter=args.package_adapter,
            )
            active_app = operation_root / "application" / "active"
            active_app.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_material["root"]), str(active_app))
            old_commit = old_material.get("commit") or args.old_source_commit
            if old_commit != EXPECTED_OLD_BASE:
                raise GateError(f"OLD_SOURCE_BASE_MISMATCH:{old_commit}")
            if old_source is not None and old_material.get("commit") and _git_dirty(old_source):
                raise GateError("OLD_SOURCE_WORKTREE_DIRTY_USE_CLEAN_BASE_OR_PACKAGE")

            _run_phase(
                phase="prepare",
                app_root=active_app,
                data_root=data_root,
                evidence_root=evidence_root,
                timeout=args.timeout_seconds,
            )
            pre_state = json.loads((evidence_root / "pre_patch_state.json").read_text(encoding="utf-8"))
            if pre_state.get("installation_id") is None:
                raise GateError("PRE_PATCH_INSTALLATION_ID_MISSING")
            if pre_state.get("backup_id") is None:
                raise GateError("PRE_PATCH_BACKUP_MISSING")
            data_root_identity = (data_root.stat().st_dev, data_root.stat().st_ino)

            new_material = _materialize(
                new_source,
                new_package,
                operation_root / "new-input",
                package_adapter=args.package_adapter,
            )
            if new_source is not None and new_material.get("commit") and _git_dirty(new_source):
                raise GateError("NEW_SOURCE_WORKTREE_DIRTY_COMMIT_BEFORE_RUNNING_GATE")
            if (
                args.new_source_commit
                and new_material.get("commit")
                and args.new_source_commit != new_material.get("commit")
            ):
                raise GateError("NEW_SOURCE_COMMIT_MISMATCH")
            staged_app = active_app.parent / "new-staged"
            shutil.move(str(new_material["root"]), str(staged_app))
            retained_old = active_app.parent / "old-stopped"
            os.replace(active_app, retained_old)
            try:
                os.replace(staged_app, active_app)
            except OSError:
                os.replace(retained_old, active_app)
                raise
            if data_root.resolve() != Path(pre_state["persistent_data_root"]).resolve():
                raise GateError("PERSISTENT_DATA_ROOT_PATH_CHANGED")
            if (data_root.stat().st_dev, data_root.stat().st_ino) != data_root_identity:
                raise GateError("PERSISTENT_DATA_ROOT_REPLACED")

            _run_phase(
                phase="verify",
                app_root=active_app,
                data_root=data_root,
                evidence_root=evidence_root,
                timeout=args.timeout_seconds,
            )
            pre_post_diff = json.loads((evidence_root / "identity_diff.json").read_text(encoding="utf-8"))
            if int(pre_post_diff.get("critical_diff_count", -1)) != 0:
                raise GateError("CRITICAL_IDENTITY_DIFF_NONZERO")
            faults = _run_fault_regression(active_app, evidence_root, args.timeout_seconds)
            post_state = json.loads((evidence_root / "post_patch_state.json").read_text(encoding="utf-8"))
            gate.update(
                {
                    "status": "PASS",
                    "old_source_commit": old_commit,
                    "new_source_commit": new_material.get("commit") or args.new_source_commit or "tree-sha256:" + new_material["sha256"],
                    "old_input_kind": old_material["kind"],
                    "old_input_id": old_material["id"],
                    "old_source_tree_sha256": old_material["sha256"] if old_material["kind"] == "SOURCE" else None,
                    "old_package_sha256": old_material["sha256"] if old_material["kind"] == "PACKAGE" else None,
                    "new_input_kind": new_material["kind"],
                    "new_input_id": new_material["id"],
                    "new_source_tree_sha256": new_material["sha256"] if new_material["kind"] == "SOURCE" else None,
                    "new_package_sha256": new_material["sha256"] if new_material["kind"] == "PACKAGE" else None,
                    "installation_id": pre_state["installation_id"],
                    "data_layout_version": pre_state["data_layout_version"],
                    "old_schema_versions": {
                        key: pre_state.get(key)
                        for key in ("hardware_schema_version", "asset_schema_version", "workbench_schema_version")
                    },
                    "new_schema_versions": {
                        key: post_state.get(key)
                        for key in ("hardware_schema_version", "asset_schema_version", "workbench_schema_version")
                    },
                    "critical_diff_count": 0,
                    "state_coverage": json.loads((evidence_root / "state_coverage.json").read_text(encoding="utf-8")),
                    "recovery_faults": {key: value.get("status") for key, value in faults.items()},
                    "persistent_data_root_preserved": True,
                    "no_empty_db_fallback": True,
                    "provider_calls_during_patch": 0,
                    "native_patch_gate": "NOT_RUN_D1_ONLY",
                }
            )
            _json_write(evidence_root / "gate_manifest.json", gate)

        _text_write(
            evidence_root / "README.md",
            "# Patch Durability Evidence\n\n"
            "This report is from the D1 source-level Harness. It is not a Windows/macOS native package certification.\n\n"
            f"- Result: {gate['status']}\n"
            f"- Installation ID: `{gate['installation_id']}`\n"
            f"- Critical identity diff count: {gate['critical_diff_count']}\n"
            f"- Persistent Data Root preserved: {gate['persistent_data_root_preserved']}\n"
            f"- Provider calls during patch/startup: {gate['provider_calls_during_patch']}\n",
        )
        missing = [name for name in REQUIRED_EVIDENCE if not (evidence_root / name).is_file()]
        if missing:
            raise GateError("EVIDENCE_FILES_MISSING:" + ",".join(missing))
        print("RESULT=PASS")
        print("PATCH_DURABILITY_HARNESS=PASS")
        print("CRITICAL_DIFF_COUNT=0")
        print("READY_FOR_D2=NO")
        return 0
    except Exception as error:
        if "evidence_root" in locals() and evidence_root.exists():
            gate_value = locals().get("gate")
            if isinstance(gate_value, dict):
                gate_value.update(status="BLOCKED", blocker=str(error), native_patch_gate="NOT_RUN_D1_ONLY")
                _json_write(evidence_root / "gate_manifest.json", gate_value)
        print("RESULT=BLOCKED")
        print("BLOCKER=" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
