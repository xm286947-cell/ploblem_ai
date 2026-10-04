#!/usr/bin/env python3
"""Verify the exact Formal ZIP and start it from a fresh native extraction."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


PACKAGE_ID_PREFIX = "HARDWARE_R1_ASSET_DURABILITY_FORMAL_"
REQUIRED_ENDPOINTS = (
    "/health",
    "/api/system/hardware/startup",
    "/p0/hardware-cases",
)
INFORMATIONAL_ENDPOINTS = ("/ready",)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _extract_safely(package: Path, app_root: Path) -> None:
    if app_root.exists() and any(app_root.iterdir()):
        raise ValueError("EXTRACT_ROOT_MUST_BE_EMPTY")
    app_root.mkdir(parents=True, exist_ok=True)
    root = app_root.resolve()
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            parts = tuple(part for part in member.parts if part not in {"", "."})
            if member.is_absolute() or ".." in parts:
                raise ValueError(f"PACKAGE_PATH_INVALID:{info.filename}")
            destination = (app_root / Path(*parts)).resolve()
            try:
                destination.relative_to(root)
            except ValueError as error:
                raise ValueError(f"PACKAGE_PATH_INVALID:{info.filename}") from error
        archive.extractall(app_root)


def _verify_content(app_root: Path, package_id: str, package_hash: str) -> dict[str, Any]:
    manifest = _read_object(app_root / "RELEASE_CONTENT_MANIFEST.json")
    if manifest.get("package_id") != package_id:
        raise ValueError("PACKAGE_ID_MISMATCH")
    source_commit = manifest.get("source_commit")
    if (
        not isinstance(source_commit, str)
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
        or package_id != f"{PACKAGE_ID_PREFIX}{source_commit[:12]}.zip"
    ):
        raise ValueError("SOURCE_COMMIT_MISMATCH")
    if manifest.get("certification_scope") != "HARDWARE_R1_ASSET_DURABILITY":
        raise ValueError("CERTIFICATION_SCOPE_MISMATCH")
    if (
        manifest.get("CERTIFICATION_STATUS") != "PENDING_NATIVE_STARTUP_BINDING"
        or manifest.get("CERTIFICATION_SCOPE") != "HARDWARE_R1_ASSET_DURABILITY"
    ):
        raise ValueError("CONTENT_MANIFEST_CERTIFICATION_STATE_MISMATCH")
    nonclaims = manifest.get("nonclaims") or {}
    expected_nonclaims = {
        "hardware_case_mvp_release": "NOT_CERTIFIED",
        "product_release_claim": "NOT_MADE",
        "installer_certification": "NOT_RUN",
        "test_package_not_release": "NOT_REUSED_AS_RELEASE",
    }
    if any(nonclaims.get(key) != value for key, value in expected_nonclaims.items()):
        raise ValueError("RELEASE_NONCLAIMS_MISMATCH")
    evidence_root = app_root / "D2_EVIDENCE"
    evidence_entries = manifest.get("d2_evidence_files")
    if not isinstance(evidence_entries, list) or not evidence_entries:
        raise ValueError("D2_EVIDENCE_INVENTORY_MISSING")
    for item in evidence_entries:
        relative = PurePosixPath(str(item.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("D2_EVIDENCE_PATH_INVALID")
        path = evidence_root.joinpath(*relative.parts)
        if not path.is_file() or _sha256(path) != item.get("sha256") or path.stat().st_size != item.get("size_bytes"):
            raise ValueError(f"D2_EVIDENCE_HASH_MISMATCH:{relative}")
    summary = _read_object(evidence_root / "native_gate_summary.json")
    if summary.get("native_patch_durability_gate") != "PASS":
        raise ValueError("EMBEDDED_D2_GATE_NOT_PASS")
    if manifest.get("d2_tested_runtime_payload_matches") is not True:
        raise ValueError("D2_TESTED_RUNTIME_PAYLOAD_NOT_BOUND")
    runtime_entries = manifest.get("runtime_payload_inventory")
    if not isinstance(runtime_entries, list) or not runtime_entries:
        raise ValueError("RUNTIME_PAYLOAD_INVENTORY_MISSING")
    digest = hashlib.sha256()
    for item in sorted(runtime_entries, key=lambda entry: str(entry.get("path") or "")):
        relative = PurePosixPath(str(item.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("RUNTIME_PAYLOAD_PATH_INVALID")
        path = app_root.joinpath(*relative.parts)
        if not path.is_file() or _sha256(path) != item.get("sha256") or path.stat().st_size != item.get("size_bytes"):
            raise ValueError(f"RUNTIME_PAYLOAD_HASH_MISMATCH:{relative}")
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["sha256"]).encode("ascii"))
        digest.update(b"\n")
    if digest.hexdigest() != manifest.get("runtime_payload_sha256"):
        raise ValueError("RUNTIME_PAYLOAD_TREE_HASH_MISMATCH")
    if _sha256(evidence_root / "native_gate_summary.json") != manifest.get("d2_native_gate_summary_sha256"):
        raise ValueError("D2_SUMMARY_HASH_MISMATCH")
    return manifest


def _get(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "HardwareR1FormalZipSmoke/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _stop_process(process: subprocess.Popen[Any], expected_platform: str) -> str:
    if process.poll() is not None:
        return "already-exited"
    if expected_platform == "windows":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return "taskkill-process-tree"
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    return "sigterm"


def run_startup_smoke(
    package: Path,
    app_root: Path,
    report_path: Path,
    expected_platform: str,
    expected_package_id: str,
    expected_package_sha256: str,
) -> dict[str, Any]:
    actual_platform = platform.system().lower()
    platform_key = "windows" if actual_platform == "windows" else "macos" if actual_platform == "darwin" else actual_platform
    if platform_key != expected_platform:
        raise ValueError(f"NATIVE_PLATFORM_MISMATCH:{expected_platform}:{platform_key}")
    if package.name != expected_package_id or _sha256(package) != expected_package_sha256:
        raise ValueError("RELEASE_PACKAGE_HASH_MISMATCH")
    content_manifest = _verify_content(app_root, expected_package_id, expected_package_sha256)
    if not (app_root / "START_HARDWARE_R1_ASSET_DURABILITY.bat").is_file():
        raise ValueError("WINDOWS_RELEASE_ENTRYPOINT_MISSING")
    shell_launcher = app_root / "START_HARDWARE_R1_ASSET_DURABILITY.sh"
    if not shell_launcher.is_file():
        raise ValueError("MACOS_RELEASE_ENTRYPOINT_MISSING")

    with tempfile.TemporaryDirectory(prefix=f"hardware-r1-release-smoke-{expected_platform}-") as temp_name:
        temp_root = Path(temp_name)
        data_root = temp_root / "persistent-data"
        home_root = temp_root / "isolated-home"
        home_root.mkdir()
        environment = dict(os.environ)
        environment.update(
            {
                "HARDWARE_DATA_ROOT": str(data_root),
                "HARDWARE_R1_PORT": "0",
                "HOME": str(home_root),
                "USERPROFILE": str(home_root),
                "LOCALAPPDATA": str(home_root / "AppData" / "Local"),
                "HARDWARE_CASE_NO_PAUSE": "1",
                "PYTHONUTF8": "1",
            }
        )
        for secret_name in (
            "HARDWARE_CASE_API_KEY",
            "HARDWARE_KNOWLEDGE_API_KEY",
            "HARDWARE_KNOWLEDGE_BASE_URL",
            "HARDWARE_KNOWLEDGE_RELEASE_VERSION",
            "HARDWARE_KNOWLEDGE_READINESS_URL",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
        ):
            environment.pop(secret_name, None)
        if expected_platform == "windows":
            environment["LOCALAPPDATA"] = str(home_root / "AppData" / "Local")
            command = ["cmd.exe", "/d", "/c", str(app_root / "START_HARDWARE_R1_ASSET_DURABILITY.bat")]
        else:
            command = ["/bin/sh", str(shell_launcher)]

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        environment["HARDWARE_R1_PORT"] = str(port)

        stdout_path = temp_root / "startup.stdout.log"
        stderr_path = temp_root / "startup.stderr.log"
        process: subprocess.Popen[Any] | None = None
        endpoints: dict[str, int] = {}
        informational_endpoints: dict[str, dict[str, Any]] = {}
        startup_payload: dict[str, Any] = {}
        failure: str | None = None
        shutdown_method: str | None = None
        started_at = time.monotonic()
        try:
            with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
                process = subprocess.Popen(
                    command,
                    cwd=app_root,
                    env=environment,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if expected_platform == "windows" else 0),
                )
                base_url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        failure = f"STARTUP_PROCESS_EXITED:{process.returncode}"
                        break
                    try:
                        code, body = _get(base_url + "/api/system/hardware/startup")
                        if code == 200:
                            startup_payload = json.loads(body.decode("utf-8"))
                            if startup_payload.get("ready") is True:
                                break
                    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                        pass
                    time.sleep(2)
                else:
                    failure = "STARTUP_TIMEOUT"
                if failure is None and startup_payload.get("ready") is not True:
                    failure = "STARTUP_READINESS_NOT_TRUE"
                if failure is None:
                    resolved_root = Path(str(startup_payload.get("data_root") or "")).expanduser().resolve(strict=False)
                    if resolved_root != data_root.resolve(strict=False):
                        failure = "STARTUP_PERSISTENT_DATA_ROOT_MISMATCH"
                if failure is None:
                    for endpoint in REQUIRED_ENDPOINTS:
                        code, _ = _get(base_url + endpoint, timeout=10)
                        endpoints[endpoint] = code
                        if code != 200:
                            failure = f"STARTUP_ENDPOINT_FAILED:{endpoint}:{code}"
                            break
                if failure is None:
                    for endpoint in INFORMATIONAL_ENDPOINTS:
                        try:
                            code, body = _get(base_url + endpoint, timeout=10)
                            try:
                                payload = json.loads(body.decode("utf-8"))
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                payload = None
                            dependencies = payload.get("dependencies") if isinstance(payload, dict) else None
                            informational_endpoints[endpoint] = {
                                "status_code": code,
                                "service_status": payload.get("status") if isinstance(payload, dict) else None,
                                "dependency_statuses": {
                                    name: value.get("status")
                                    for name, value in (dependencies or {}).items()
                                    if isinstance(value, dict)
                                },
                                "error_codes": {
                                    name: value.get("error_code")
                                    for name, value in (dependencies or {}).items()
                                    if isinstance(value, dict) and value.get("error_code")
                                },
                            }
                        except OSError as error:
                            informational_endpoints[endpoint] = {
                                "status_code": None,
                                "error": type(error).__name__,
                            }
        except (OSError, subprocess.SubprocessError) as error:
            failure = f"STARTUP_LAUNCH_FAILED:{type(error).__name__}"
        finally:
            if process is not None:
                shutdown_method = _stop_process(process, expected_platform)

        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")[-4000:] if stdout_path.exists() else ""
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:] if stderr_path.exists() else ""
        report = {
            "contract_version": "hardware-r1-formal-zip-native-startup/v1",
            "status": "PASS" if failure is None else "BLOCKED",
            "platform": expected_platform,
            "package_id": expected_package_id,
            "package_sha256": expected_package_sha256,
            "source_commit": content_manifest.get("source_commit"),
            "certification_scope": "HARDWARE_R1_ASSET_DURABILITY",
            "fresh_extract": True,
            "persistent_data_root_isolated": (
                data_root.parent == temp_root
                and not data_root.resolve(strict=False).is_relative_to(app_root.resolve(strict=False))
            ),
            "persistent_data_root": startup_payload.get("data_root") or str(data_root),
            "installation_id": startup_payload.get("installation_id"),
            "startup_status": startup_payload.get("status"),
            "startup_ready": startup_payload.get("ready"),
            "endpoints": endpoints,
            "informational_endpoints": informational_endpoints,
            "product_readiness": "NOT_CERTIFIED_OUT_OF_SCOPE",
            "launcher": "START_HARDWARE_R1_ASSET_DURABILITY.bat" if expected_platform == "windows" else "START_HARDWARE_R1_ASSET_DURABILITY.sh",
            "runtime_payload_sha256": content_manifest.get("runtime_payload_sha256"),
            "d2_evidence_tree_sha256": content_manifest.get("d2_evidence_tree_sha256"),
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "shutdown_method": shutdown_method,
            "failure": failure,
            "stdout_tail": stdout,
            "stderr_tail": stderr,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if failure:
            raise ValueError(failure)
        if not report["installation_id"]:
            raise ValueError("STARTUP_INSTALLATION_ID_MISSING")
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True, help="Fresh extraction directory for the supplied ZIP")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--platform", choices=("windows", "macos"), required=True)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--package-sha256", required=True)
    parser.add_argument("--already-extracted", action="store_true")
    args = parser.parse_args()
    try:
        if not args.already_extracted:
            _extract_safely(args.package, args.app_root)
        report = run_startup_smoke(
            args.package.resolve(),
            args.app_root.resolve(),
            args.report,
            args.platform,
            args.package_id,
            args.package_sha256,
        )
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        print("RESULT=BLOCKED")
        print(f"BLOCKER={error}")
        return 2
    print(f"NATIVE_STARTUP={report['status']}")
    print(f"PLATFORM={args.platform}")
    print(f"PACKAGE_ID={args.package_id}")
    print(f"PACKAGE_SHA256={args.package_sha256}")
    print(f"INSTALLATION_ID={report['installation_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
