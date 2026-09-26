from __future__ import annotations

import argparse
import os
import signal
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path


PACKAGE_DIR = "HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1"
POSIX_ENTRYPOINTS = (
    "INIT_LOCAL_CONFIG.sh",
    "START_HARDWARE_CASE.sh",
    "RUN_REAL_AI_VALIDATION.sh",
    "run_hardware_case_mvp_smoke.sh",
    "run_hardware_case_product_test.sh",
)
WINDOWS_ENTRYPOINTS = (
    "INIT_LOCAL_CONFIG.bat",
    "CHECK_ENV.bat",
    "START_HARDWARE_CASE.bat",
    "RUN_REAL_AI_VALIDATION.bat",
    "run_hardware_case_mvp_smoke.bat",
    "run_hardware_case_product_test.bat",
)


def package_archive(explicit: str | None) -> Path:
    if explicit:
        archive = Path(explicit)
    else:
        candidates = sorted(
            Path("dist").glob("HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1_*.zip"),
            key=lambda item: item.stat().st_mtime,
        )
        if not candidates:
            raise SystemExit("PACKAGE_ARCHIVE_MISSING")
        archive = candidates[-1]
    if not archive.is_file():
        raise SystemExit(f"PACKAGE_ARCHIVE_MISSING={archive}")
    return archive


def check_zip_modes(archive: Path) -> None:
    prefix = f"{PACKAGE_DIR}/"
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        for name in POSIX_ENTRYPOINTS:
            path = prefix + name
            if path not in names:
                raise SystemExit(f"ZIP_ENTRY_MISSING={path}")
            info = bundle.getinfo(path)
            mode = (info.external_attr >> 16) & 0o777
            if mode & 0o111 != 0o111:
                raise SystemExit(f"ZIP_ENTRY_NOT_EXECUTABLE={path}:{mode:o}")
        for name in WINDOWS_ENTRYPOINTS:
            if prefix + name not in names:
                raise SystemExit(f"WINDOWS_ENTRY_MISSING={name}")


def kill_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def text_output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_startup_launcher(root: Path) -> str:
    launcher = root / "START_HARDWARE_CASE.sh"
    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        [str(launcher)],
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=15)
    except subprocess.TimeoutExpired as exc:
        output = text_output(exc.stdout)
        kill_process_group(process)
        tail = text_output(process.stdout.read() if process.stdout else "")
        output = f"{output}{tail}"
        # A running server after the precheck is expected. Timeout is therefore
        # accepted only when the executable reached the normal precheck path.
        if "[PASS] Python" not in output and "[FAIL] Python" not in output:
            raise SystemExit("START_LAUNCHER_DID_NOT_REACH_PRECHECK") from exc
        return output
    if process.returncode in (126, 127):
        raise SystemExit(f"START_LAUNCHER_EXECUTION_FAILED={process.returncode}")
    if "[PASS] Python" not in output and "[FAIL] Python" not in output:
        raise SystemExit("START_LAUNCHER_DID_NOT_REACH_PRECHECK")
    return output


def check_fresh_extract(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="hardware-case-launcher-gate-") as temp:
        target = Path(temp)
        extracted = subprocess.run(
            ["unzip", "-q", str(archive.resolve()), "-d", str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if extracted.returncode:
            raise SystemExit(f"FRESH_EXTRACT_FAILED={extracted.stderr.strip()}")
        root = target / PACKAGE_DIR

        for name in POSIX_ENTRYPOINTS:
            mode = stat.S_IMODE((root / name).stat().st_mode)
            if mode & 0o111 != 0o111:
                raise SystemExit(f"FRESH_EXTRACT_NOT_EXECUTABLE={name}:{mode:o}")

        init = subprocess.run(
            [str(root / "INIT_LOCAL_CONFIG.sh")],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if init.returncode != 0 or "Local configuration initialized." not in init.stdout:
            raise SystemExit(f"INIT_LAUNCHER_FAILED={init.returncode}:{init.stderr.strip()}")

        startup_output = run_startup_launcher(root)
        if "Permission denied" in startup_output:
            raise SystemExit("START_LAUNCHER_PERMISSION_DENIED")

        real = subprocess.run(
            [str(root / "RUN_REAL_AI_VALIDATION.sh")],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        real_output = f"{real.stdout}\n{real.stderr}"
        if real.returncode in (126, 127) or "Permission denied" in real_output:
            raise SystemExit(f"REAL_AI_LAUNCHER_EXECUTION_FAILED={real.returncode}")
        if "MODEL_NAME_NOT_CONFIGURED" not in real_output:
            raise SystemExit("REAL_AI_LAUNCHER_DID_NOT_REACH_NORMAL_CONFIG_PRECHECK")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", nargs="?")
    args = parser.parse_args()
    archive = package_archive(args.archive)
    check_zip_modes(archive)
    check_fresh_extract(archive)
    print("ZIP_POSIX_MODES=PASS")
    print("FRESH_EXTRACT_POSIX_MODES=PASS")
    print("INIT_LOCAL_CONFIG_DIRECT_LAUNCH=PASS")
    print("START_HARDWARE_CASE_PRECHECK=PASS")
    print("RUN_REAL_AI_CONFIG_PRECHECK=PASS")
    print("WINDOWS_LAUNCHER_ENTRIES=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
