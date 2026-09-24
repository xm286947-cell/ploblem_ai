"""Exercise the RC2 POSIX launchers on Linux/macOS with no chmod repair."""
from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import stat
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8769)
    args = parser.parse_args()

    package = args.package.resolve()
    system = platform.system().lower()
    if system == "linux":
        launcher = package / "run_linux.sh"
        result_key = "LINUX_NATIVE"
    elif system == "darwin":
        launcher = package / "run_macos.sh"
        result_key = "MACOS_NATIVE"
    else:
        raise RuntimeError(f"UNSUPPORTED_POSIX_PLATFORM:{system}")

    mode = stat.S_IMODE(launcher.stat().st_mode)
    if not os.access(launcher, os.X_OK):
        raise RuntimeError(f"NO_EXECUTE_PERMISSION:{launcher}:{oct(mode)}")

    log_path = package.parent / f"{system}_startup.log"
    address = f"http://127.0.0.1:{args.port}"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(launcher), "--no-browser", "--port", str(args.port)],
            cwd=package,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"POSIX_STARTUP_EXITED:{process.returncode}")
                try:
                    status, _ = get(address + "/api/v2/initialization/status")
                    if status == 200:
                        break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(2)
            else:
                raise RuntimeError("POSIX_STARTUP_TIMEOUT")

            assert get(address + "/p0/issues/K-ITR-1")[0] == 200
            assert get(address + "/p0/cases")[0] == 200

            request = urllib.request.Request(
                address + "/api/v2/issues/K-ITR-1/repeat-risk/queries",
                data=json.dumps({"include_missed_test": True, "top_k": 5}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.load(response)["result"]
            assert result["result_status"] == "READY_FOR_REVIEW"
            assert result["candidates"][0]["why_relevant"]
            assert result["candidates"][0]["evidence"]

            cases = json.loads(get(address + "/api/v2/historical-cases")[1])
            assert cases["total"] >= 1
            assert all(item["status"] == "PUBLISHED" for item in cases["items"])

            print(f"{result_key}=PASS")
            print("NO_CHMOD_DIRECT_START=PASS")
            print("WEB_STARTUP=PASS")
            print("REPEAT_SMOKE=PASS")
            print("CASE_LIBRARY_SMOKE=PASS")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    raise RuntimeError("POSIX_SHUTDOWN_TIMEOUT")

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    forbidden = ("Permission denied", "PermissionError", "Traceback (most recent call last)")
    findings = [item for item in forbidden if item in log_text]
    if findings:
        raise RuntimeError("POSIX_LOG_FAILURE:" + ",".join(findings))

    db = package / "data/runtime/quality_capability_p0.sqlite3"
    assert db.is_file()
    probe = db.with_suffix(".sqlite3.probe")
    os.replace(db, probe)
    os.replace(probe, db)

    print("SHUTDOWN_CLEANUP=PASS")
    print(f"LAUNCHER_MODE={oct(mode)}")
    print(f"PLATFORM_LOG={log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
