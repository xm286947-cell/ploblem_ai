from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_DIR = "HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1"


def _archive() -> Path:
    candidates = sorted(
        DIST.glob("HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1_*.zip"),
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

    gate_output = _run(
        [sys.executable, "scripts/hardware_case_fresh_extract_gate.py"],
        root,
    )
    required_markers = (
        "FRESH_EXTRACT_STARTUP=PASS",
        "HEALTH=PASS",
        "READINESS=PASS",
        "DEPENDENCY_UNREADY=PASS",
        "PUBLIC_CONTRACT_BINDING=PASS",
        "UNKNOWN_VERSION_FAIL_CLOSED=PASS",
        "CONFIG_VALIDATION=PASS",
        "SECRET_SAFETY=PASS",
    )
    for marker in required_markers:
        if marker not in gate_output:
            raise SystemExit("FRESH_EXTRACT_MARKER_MISSING=" + marker)

    if system == "Windows":
        env = {**os.environ, "HARDWARE_CASE_NO_PAUSE": "1"}
        output = _run(["cmd", "/c", "CHECK_ENV.bat", "web"], root, env)
        if "RESULT=PASS" not in output:
            raise SystemExit("WINDOWS_PRECHECK_NOT_PASS")
    else:
        _run(["sh", "-n", "START_HARDWARE_CASE.sh"], root)
        _run(["sh", "-n", "START_HARDWARE_CASE.command"], root)
        mode = (root / "START_HARDWARE_CASE.command").stat().st_mode
        if mode & 0o111 == 0:
            # zipfile extraction may not restore mode on every host; the
            # archive mode is separately validated by the product package gate.
            print("MACOS_EXTRACT_MODE=HOST_DEPENDENT")
        else:
            print("MACOS_COMMAND_EXECUTABLE=PASS")

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
