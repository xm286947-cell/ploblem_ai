from __future__ import annotations

import json
import os
import platform
import socket
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_DIR = "HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1"


def _archive() -> Path:
    candidates = sorted(
        [
            *DIST.glob("HARDWARE_CASE_PRODUCT_TEST_FULL_R1_*.zip"),
            *DIST.glob("HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1_*.zip"),
        ],
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        raise SystemExit("PACKAGE_ARCHIVE_MISSING")
    return candidates[-1]


def _run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode != 0:
        raise SystemExit(
            "COMMAND_FAILED="
            + " ".join(command)
            + "|"
            + output[-1500:].replace("\n", " ")
        )
    return output


def _verify_native_launcher(root: Path, system: str) -> None:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = int(candidate.getsockname()[1])
    with tempfile.TemporaryDirectory(prefix="hardware-launcher-data-") as temp:
        temp_root = Path(temp)
        isolated_home = temp_root / "home"
        isolated_home.mkdir()
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        for name in (
            "HARDWARE_CASE_MODEL_CONFIG", "HARDWARE_CASE_AGENT_CONFIG",
            "HARDWARE_CASE_RUNTIME_DB", "HARDWARE_CASE_API_KEY",
            "HARDWARE_KNOWLEDGE_BASE_URL", "HARDWARE_KNOWLEDGE_READINESS_URL",
        ):
            env.pop(name, None)
        env.update(
            {
                "HARDWARE_CASE_HOST": "127.0.0.1",
                "HARDWARE_CASE_PORT": str(port),
                "HARDWARE_CASE_NO_PAUSE": "1",
                "HARDWARE_CASE_NO_BROWSER": "1",
                "HARDWARE_DATA_ROOT": str(temp_root / "data"),
                "HOME": str(isolated_home),
                "USERPROFILE": str(isolated_home),
                "LOCALAPPDATA": str(isolated_home / "AppData" / "Local"),
                "APPDATA": str(isolated_home / "AppData" / "Roaming"),
                "XDG_CONFIG_HOME": str(isolated_home / ".config"),
                "XDG_DATA_HOME": str(isolated_home / ".local" / "share"),
                "HARDWARE_R1_E2E_PROFILE": "1",
                "HARDWARE_R1_E2E_KNOWLEDGE_ENV": "NON_PROD",
                "HARDWARE_R1_E2E_KNOWLEDGE_MODE": "LOCAL_NON_PROD",
                "HARDWARE_KNOWLEDGE_RELEASE_VERSION": "CI_LAUNCHER_GATE",
            }
        )
        config = root / "config/runtime/model.local.yaml"
        if config.exists():
            raise SystemExit("LAUNCHER_CONFIG_WAS_MANUALLY_PREPARED")

        if system == "Windows":
            command = ["cmd", "/c", "START_HARDWARE_CASE.bat"]
        elif system == "Darwin":
            command = ["./START_HARDWARE_CASE.command"]
        else:
            command = ["sh", "START_HARDWARE_CASE.sh"]
        log_path = temp_root / "launcher.log"
        process = None
        with log_path.open("w+", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=root,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=(system != "Windows"),
            )
            try:
                deadline = time.monotonic() + 75
                health = ready = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                            health = json.loads(response.read().decode("utf-8"))
                        try:
                            with urlopen(f"http://127.0.0.1:{port}/ready", timeout=2) as response:
                                ready = json.loads(response.read().decode("utf-8"))
                        except HTTPError as response:
                            ready = json.loads(response.read().decode("utf-8"))
                    except (URLError, TimeoutError, OSError, ValueError):
                        time.sleep(0.5)
                        continue
                    if health is not None and ready is not None and ready.get("status") == "READY":
                        break
                log.flush()
                if (
                    process.poll() is not None
                    or health is None
                    or ready is None
                    or ready.get("status") != "READY"
                ):
                    log.seek(0)
                    detail = log.read()[-1800:]
                    readiness = json.dumps(ready, ensure_ascii=False) if ready is not None else "UNAVAILABLE"
                    raise SystemExit(
                        "NATIVE_LAUNCHER_START_FAIL=ready:"
                        + readiness
                        + ";log:"
                        + detail.replace("\n", " ")
                    )
                if health.get("status") != "HEALTHY":
                    raise SystemExit("NATIVE_LAUNCHER_HEALTH_FAIL")
                if ready.get("status") != "READY":
                    raise SystemExit("NATIVE_LAUNCHER_READY_FAIL=" + json.dumps(ready))
                runtime = ready.get("dependencies", {}).get("UNIFIED_RUNTIME_CONFIG", {})
                if runtime.get("configuration_state") != "CONFIGURATION_REQUIRED":
                    raise SystemExit("NATIVE_LAUNCHER_BOOTSTRAP_STATE_MISSING")
                if not config.is_file():
                    raise SystemExit("NATIVE_LAUNCHER_CONFIG_BOOTSTRAP_FAIL")
                if urlopen(f"http://127.0.0.1:{port}/p0/hardware-cases", timeout=2).status != 200:
                    raise SystemExit("NATIVE_LAUNCHER_PRODUCT_ENTRY_FAIL")
            finally:
                if process.poll() is None:
                    if system == "Windows":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                        )
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def main() -> int:
    system = platform.system()
    archive = _archive()
    target = DIST / ("fresh-extract-" + system)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)

    root = target / PACKAGE_DIR
    if not root.is_dir():
        raise SystemExit("FRESH_EXTRACT_PACKAGE_ROOT_MISSING")

    gate_env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    gate_output = _run(
        [sys.executable, "scripts/hardware_case_fresh_extract_gate.py"],
        root,
        gate_env,
    )
    required_markers = (
        "FRESH_EXTRACT_STARTUP=PASS",
        "PACKAGE_IMPORT=PASS",
        "RECURSIVE_LOCAL_IMPORT_CLOSURE=PASS",
        "HEALTH=PASS",
        "READY=PASS_PER_FROZEN_CONTRACT",
        "MODEL_CONFIG_BOOTSTRAP_CONTRACT=NON_SECRET_LOOPBACK_TO_LOCAL_CONFIG",
        "MISSING_CONFIG_FILE_CRASH=NO",
        "PRODUCT_SMOKE=PASS",
        "SECRET_SAFETY=PASS",
    )
    for marker in required_markers:
        if marker not in gate_output:
            raise SystemExit("FRESH_EXTRACT_MARKER_MISSING=" + marker)

    if system == "Windows":
        env = {**gate_env, "HARDWARE_CASE_NO_PAUSE": "1"}
        output = _run(["cmd", "/c", "CHECK_ENV.bat", "web"], root, env)
        if "RESULT=PASS" not in output:
            raise SystemExit("WINDOWS_PRECHECK_NOT_PASS")
    else:
        _run(["sh", "-n", "START_HARDWARE_CASE.sh"], root)
        _run(["sh", "-n", "START_HARDWARE_CASE.command"], root)
        # zipfile.extractall() does not restore POSIX mode bits. Apply the
        # archived permissions before exercising the native macOS entrypoint.
        posix_entrypoints = (
            "INIT_LOCAL_CONFIG.sh",
            "START_HARDWARE_CASE.sh",
            "START_HARDWARE_CASE.command",
        )
        with zipfile.ZipFile(archive) as bundle:
            for name in posix_entrypoints:
                info = bundle.getinfo(f"{PACKAGE_DIR}/{name}")
                mode = (info.external_attr >> 16) & 0o777
                if mode & 0o111 != 0o111:
                    raise SystemExit("MACOS_ARCHIVE_ENTRY_NOT_EXECUTABLE=" + name)
                (root / name).chmod(mode)
        if (root / "START_HARDWARE_CASE.command").stat().st_mode & 0o111 != 0o111:
            raise SystemExit("MACOS_COMMAND_EXECUTABLE=FAIL")
        print("MACOS_COMMAND_EXECUTABLE=PASS")

    _verify_native_launcher(root, system)
    print("SERVER_START=PASS")
    print("HEALTH=PASS")
    print("READY=PASS_PER_FROZEN_CONTRACT")
    print(("WINDOWS_LAUNCHER" if system == "Windows" else "MACOS_LAUNCHER") + "=PASS")

    manifest = json.loads((root / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    semantics = {
        "source_commit": manifest["source_commit"],
        "product_version": manifest["product_version"],
        "public_contract_version": manifest["public_contract_version"],
        "schema_version": manifest["schema_version"],
        "web_entry": manifest["web_entry"],
        "api_prefix": manifest["api_prefix"],
        "public_api_prefix": manifest["public_api_prefix"],
        "system_endpoints": manifest["system_endpoints"],
        "release_semantics": manifest["release_semantics"],
        "config_lifecycle": manifest["config_lifecycle"],
        "entrypoints": {
            "windows": manifest["entrypoints"]["start_product_windows"],
            "shell": manifest["entrypoints"]["start_product_shell"],
            "macos": manifest["entrypoints"]["start_product_macos"],
            "web_launcher": manifest["entrypoints"]["web_launcher"],
        },
    }
    output_path = DIST / f"cross-platform-semantics-{system}.json"
    output_path.write_text(
        json.dumps(semantics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"PLATFORM={system}")
    print("FRESH_EXTRACT=PASS")
    print(f"SEMANTICS_FILE={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
