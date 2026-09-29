"""Windows launcher/preflight/smoke controller for Overall R2 W5 candidate."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "http://127.0.0.1:8080/p0/overall"


def _local_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return Path(os.environ.get("OVERALL_R2_DATA_DIR") or (base / "OverallR2" / "data")).expanduser().resolve()


def verify_package_binding(package_root: Path) -> dict[str, str]:
    root = package_root.expanduser().resolve()
    manifest_path = root / "OVERALL_R2_RELEASE_CANDIDATE_MANIFEST.json"
    marker = root / "R2_SOURCE_COMMIT"
    if not manifest_path.is_file():
        raise RuntimeError("R2_RELEASE_CANDIDATE_MANIFEST_MISSING")
    if not marker.is_file():
        raise RuntimeError("R2_SOURCE_COMMIT_MARKER_MISSING")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("R2_RELEASE_CANDIDATE_MANIFEST_INVALID") from exc
    if manifest.get("contract") != "overall-r2-release-candidate/v1":
        raise RuntimeError("R2_RELEASE_CANDIDATE_CONTRACT_INVALID")
    expected = str(manifest.get("dut_source_commit") or "").strip()
    actual = marker.read_text(encoding="utf-8").strip()
    if not expected or expected != actual:
        raise RuntimeError(
            f"R2_SOURCE_BINDING_MISMATCH:expected={expected};actual={actual}"
        )

    expected_runtime = (root / "runtime" / "__init__.py").resolve()
    if not expected_runtime.is_file():
        raise RuntimeError("SHARED_RUNTIME_SOURCE_MISSING")
    if (root / "vendor" / "unified_agent_runtime" / "runtime" / "__init__.py").exists():
        raise RuntimeError("DUPLICATE_VENDOR_RUNTIME_PRESENT")

    root_text = str(root)
    if root_text in sys.path:
        sys.path.remove(root_text)
    sys.path.insert(0, root_text)
    import runtime

    actual_runtime = Path(runtime.__file__).resolve()
    if actual_runtime != expected_runtime:
        raise RuntimeError(
            "RUNTIME_IMPORT_SOURCE_MISMATCH:"
            f"expected={expected_runtime};actual={actual_runtime}"
        )
    return {
        "source_commit": actual,
        "runtime_source": str(actual_runtime),
    }


def resolve_bindings(package_root: Path) -> dict[str, Path]:
    root = package_root.expanduser().resolve()
    data_root = _local_root()
    p1_db = Path(
        os.environ.get("OVERALL_R2_P1_DB")
        or (data_root / "quality_capability_p1.db")
    ).expanduser().resolve()
    storage_data = Path(
        os.environ.get("STORAGE_LIFE_DATA_DIR")
        or (data_root / "storage")
    ).expanduser().resolve()
    storage_runtime_db = Path(
        os.environ.get("STORAGE_LIFE_RUNTIME_DB")
        or (data_root / "runtime" / "storage_runtime.sqlite3")
    ).expanduser().resolve()
    knowledge_repository = Path(
        os.environ.get("STORAGE_KNOWLEDGE_REPOSITORY_DIR")
        or (data_root / "knowledge_repository")
    ).expanduser().resolve()
    knowledge_release = Path(
        os.environ.get("STORAGE_KNOWLEDGE_RELEASE_DIR")
        or (data_root / "knowledge_release" / "current")
    ).expanduser().resolve()
    runtime_control_root = Path(
        os.environ.get("OVERALL_RUNTIME_CONTROL_ROOT")
        or (data_root / "overall_runtime_control")
    ).expanduser().resolve()
    legacy_raw = os.environ.get("LEGACY_QUALITY_ISSUE_DB_PATH", "").strip()
    if not legacy_raw:
        raise RuntimeError("LEGACY_DB_PATH_NOT_CONFIGURED")
    legacy_db = Path(legacy_raw).expanduser().resolve()
    if not legacy_db.is_file():
        raise RuntimeError("LEGACY_DB_MISSING")

    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from quality_knowledge.web.legacy_database_binding import (
        LegacyDatabaseBindingError,
        validate_legacy_database,
    )

    try:
        _, error = validate_legacy_database(p1_db, legacy_db)
    except LegacyDatabaseBindingError as exc:
        raise RuntimeError(exc.code) from exc
    if error:
        raise RuntimeError(error)

    data_root.mkdir(parents=True, exist_ok=True)
    p1_db.parent.mkdir(parents=True, exist_ok=True)
    storage_data.mkdir(parents=True, exist_ok=True)
    storage_runtime_db.parent.mkdir(parents=True, exist_ok=True)
    knowledge_repository.mkdir(parents=True, exist_ok=True)
    knowledge_release.parent.mkdir(parents=True, exist_ok=True)
    runtime_control_root.mkdir(parents=True, exist_ok=True)
    return {
        "data_root": data_root,
        "p1_db": p1_db,
        "storage_data": storage_data,
        "storage_runtime_db": storage_runtime_db,
        "knowledge_repository": knowledge_repository,
        "knowledge_release": knowledge_release,
        "runtime_control_root": runtime_control_root,
        "legacy_db": legacy_db,
    }


def _active_runtime_model_config(
    package_root: Path,
    control_root: Path,
) -> Path:
    canonical = (
        package_root / "config" / "runtime" / "model.yaml"
    ).resolve()
    active = control_root / "active.json"
    if not active.is_file():
        return canonical
    try:
        pointer = json.loads(active.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("OVERALL_RUNTIME_ACTIVE_POINTER_INVALID") from exc
    if str(pointer.get("revision_id") or "") == "CANONICAL":
        return canonical
    path = Path(str(pointer.get("path") or "")).expanduser().resolve()
    revision_root = (control_root / "revisions").resolve()
    if (
        not path.is_file()
        or revision_root not in path.parents
    ):
        raise RuntimeError("OVERALL_RUNTIME_ACTIVE_REVISION_INVALID")
    return path


def _pid_file(data_root: Path) -> Path:
    return data_root / "overall-r2.pid"


def _read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def stop_running(data_root: Path) -> int:
    pid_path = _pid_file(data_root)
    pid = _read_pid(pid_path)
    if pid is None:
        print("STOP=NO_RUNNING_PID")
        return 0
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode not in {0, 128}:
            print(result.stdout, file=sys.stderr)
            return result.returncode
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        pid_path.unlink()
    except OSError:
        pass
    print(f"STOPPED_PID={pid}")
    return 0


def _wait_ready(url: str, process: subprocess.Popen[Any], timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"APPLICATION_EXITED_EARLY:{process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"APPLICATION_START_TIMEOUT:{last_error}")


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP_STATUS:{url}:{response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON_OBJECT_REQUIRED:{url}")
    return payload


def _smoke(base_url: str) -> None:
    routes = (
        "/p0/overall",
        "/p0/issues",
        "/p0/itr-recovery",
        "/p0/itr-resolution",
        "/p0/software-assessment",
        "/p0/missed-test-analysis",
        "/p0/batch-analysis",
        "/p0/cases",
        "/p0/quality-scenario-insights",
        "/p0/hardware-cases",
        "/storage-workspace/api/health",
        "/analysis",
    )
    for path in routes:
        url = base_url + path
        with urllib.request.urlopen(url, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError(f"SMOKE_ROUTE_FAILED:{path}:{response.status}")
        print(f"SMOKE_ROUTE={path}=PASS")

    assembly = _get_json(base_url + "/api/v2/overall/assembly")
    if assembly.get("contract_version") != "overall-domain-assembly/v1":
        raise RuntimeError("ASSEMBLY_CONTRACT_INVALID")
    if assembly.get("total") != 4 or assembly.get("ready") != 4 or not assembly.get("all_ready"):
        raise RuntimeError(
            "DOMAIN_ASSEMBLY_NOT_READY:"
            f"ready={assembly.get('ready')};total={assembly.get('total')}"
        )
    print("DOMAIN_ASSEMBLY=4/4_READY")

    if os.environ.get("HARDWARE_CASE_HOST_ROLE", "CONSUMER").strip().upper() == "MAINTAINER":
        for path in (
            "/p0/hardware-cases/base-data",
            "/p0/hardware-cases/intake",
            "/p0/hardware-cases/review",
        ):
            with urllib.request.urlopen(base_url + path, timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"HARDWARE_MAINTENANCE_ROUTE_FAILED:{path}:{response.status}"
                    )
            print(f"HARDWARE_MAINTENANCE_ROUTE={path}=PASS")
        print("HARDWARE_MAINTAINER_ENTRY=PASS")

    storage_runtime = _get_json(
        base_url + "/storage-workspace/api/v1/runtime/status"
    )
    if storage_runtime.get("execution_mode") != "runtime":
        raise RuntimeError("STORAGE_RUNTIME_MODE_NOT_RUNTIME")
    if not storage_runtime.get("configured"):
        raise RuntimeError(
            "STORAGE_AGENT_NOT_CONFIGURED:"
            + str(storage_runtime.get("error") or "UNKNOWN")
        )
    agents = set(storage_runtime.get("agents") or [])
    if "storage.emmc.parameter_extract" not in agents:
        raise RuntimeError("STORAGE_EMMC_AGENT_NOT_RESOLVED")
    if storage_runtime.get("profile") != "qwen_prod":
        raise RuntimeError(
            "STORAGE_MODEL_REF_NOT_QWEN_PROD:"
            + str(storage_runtime.get("profile"))
        )
    runtime_binding = storage_runtime.get("runtime") or {}
    if runtime_binding.get("source_binding") != "OVERALL_R2_SOURCE_COMMIT":
        raise RuntimeError(
            "STORAGE_RUNTIME_SOURCE_BINDING_INVALID:"
            + str(runtime_binding.get("source_binding"))
        )
    print("STORAGE_AGENT_CONFIGURED=PASS")
    print("STORAGE_AGENT_ID=storage.emmc.parameter_extract=PASS")
    print("STORAGE_MODEL_REF=qwen_prod=PASS")
    print("STORAGE_RUNTIME_SOURCE_BINDING=OVERALL_R2_SOURCE_COMMIT=PASS")
    print("PROVIDER_CONFIG_RESOLVED=PASS")
    print("PROVIDER_CONNECTIVITY=NOT_RUN_W5_SMOKE")


def build_process_env(
    package_root: Path,
    bindings: dict[str, Path],
) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(package_root)
    env["LEGACY_QUALITY_ISSUE_DB_PATH"] = str(bindings["legacy_db"])
    env["STORAGE_LIFE_DATA_DIR"] = str(bindings["storage_data"])
    env["STORAGE_LIFE_EXECUTION_MODE"] = "runtime"
    env["UNIFIED_AGENT_RUNTIME_ROOT"] = str(package_root)
    control_root = Path(
        bindings.get("runtime_control_root")
        or (
            Path(bindings["storage_data"]).resolve().parent
            / "overall_runtime_control"
        )
    ).resolve()
    control_root.mkdir(parents=True, exist_ok=True)
    env["OVERALL_RUNTIME_CONTROL_ROOT"] = str(control_root)
    active_model_config = _active_runtime_model_config(
        package_root,
        control_root,
    )
    for name in (
        "MAJOR_MODEL_CONFIG",
        "HARDWARE_CASE_MODEL_CONFIG",
        "STORAGE_MODEL_CONFIG",
    ):
        explicit = os.environ.get(name, "").strip()
        env[name] = explicit or str(active_model_config)
    env["OVERALL_RUNTIME_MODEL_CONFIG"] = str(active_model_config)
    env["STORAGE_MODEL_CONFIG_SOURCE"] = (
        "OPERATOR_OVERRIDE"
        if os.environ.get("STORAGE_MODEL_CONFIG", "").strip()
        else "OVERALL_AGENT_CONFIG"
    )
    env["STORAGE_LIFE_RUNTIME_DB"] = str(bindings["storage_runtime_db"])
    env["STORAGE_KNOWLEDGE_REPOSITORY_DIR"] = str(
        bindings["knowledge_repository"]
    )
    env["STORAGE_KNOWLEDGE_RELEASE_DIR"] = str(bindings["knowledge_release"])
    hardware_role = os.environ.get(
        "HARDWARE_CASE_HOST_ROLE",
        "CONSUMER",
    ).strip().upper()
    if hardware_role not in {"CONSUMER", "MAINTAINER"}:
        raise RuntimeError("HARDWARE_CASE_HOST_ROLE_INVALID")
    env["HARDWARE_CASE_HOST_ROLE"] = hardware_role
    env["REPEAT_CASE_NO_PAUSE"] = "1"
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, default=ROOT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()

    package_root = args.package_root.expanduser().resolve()
    try:
        binding = verify_package_binding(package_root)
    except Exception as exc:
        print(f"PREFLIGHT=FAIL:{exc}", file=sys.stderr)
        return 6

    if args.stop:
        return stop_running(_local_root())

    try:
        bindings = resolve_bindings(package_root)
    except Exception as exc:
        print(f"PREFLIGHT=FAIL:{exc}", file=sys.stderr)
        return 6

    print("PREFLIGHT=PASS")
    print(f"SOURCE_COMMIT={binding['source_commit']}")
    print(f"RUNTIME_SOURCE={binding['runtime_source']}")
    print(f"LEGACY_DB={bindings['legacy_db']}")
    print(f"P1_DB={bindings['p1_db']}")
    print(f"STORAGE_DATA={bindings['storage_data']}")
    print(f"STORAGE_RUNTIME_DB={bindings['storage_runtime_db']}")
    print(f"KNOWLEDGE_REPOSITORY={bindings['knowledge_repository']}")
    print(f"KNOWLEDGE_RELEASE={bindings['knowledge_release']}")
    print(f"RUNTIME_CONTROL_ROOT={bindings['runtime_control_root']}")
    if args.check_only:
        return 0

    env = build_process_env(package_root, bindings)

    log_dir = bindings["data_root"].parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "overall-r2.log"
    base_url = f"http://{args.host}:{args.port}"
    command = [
        sys.executable,
        str(package_root / "main.py"),
        "knowledge-p1-start",
        "--db",
        str(bindings["p1_db"]),
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]

    with log_path.open("a", encoding="utf-8", errors="replace", buffering=1) as log:
        process = subprocess.Popen(
            command,
            cwd=package_root,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        pid_path = _pid_file(bindings["data_root"])
        pid_path.write_text(str(process.pid), encoding="ascii")
        try:
            _wait_ready(base_url + "/p0/overall", process)
            print("STARTUP=PASS")
            print(f"PID={process.pid}")
            print(f"URL={base_url}/p0/overall")
            if args.smoke:
                _smoke(base_url)
                print("WINDOWS_CLEAN_START_SMOKE=PASS")
                return 0
            if not args.no_browser:
                webbrowser.open(base_url + "/p0/overall")
            print("Press Ctrl+C in this window or run STOP_OVERALL_R2_WINDOWS.bat.")
            return process.wait()
        except KeyboardInterrupt:
            print("STOPPING=USER_REQUEST")
            return 0
        except Exception as exc:
            print(f"STARTUP=FAIL:{exc}", file=sys.stderr)
            print(f"LOG={log_path}", file=sys.stderr)
            return 5
        finally:
            if args.smoke and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.poll() is not None:
                try:
                    pid_path.unlink()
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
