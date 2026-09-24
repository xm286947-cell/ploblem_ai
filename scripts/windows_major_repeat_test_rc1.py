"""Exercise the RC1 batch entrypoint on a Windows CI runner."""
from __future__ import annotations

import argparse
import json
import os
import signal
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
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    package = args.package.resolve()
    log_path = package.parent / "windows_startup.log"
    address = f"http://127.0.0.1:{args.port}"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            ["cmd.exe", "/c", str(package / "run_windows.bat"), "--no-browser", "--port", str(args.port)],
            cwd=package,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"WINDOWS_STARTUP_EXITED:{process.returncode}")
                try:
                    status, _ = get(address + "/api/v2/initialization/status")
                    if status == 200:
                        break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(2)
            else:
                raise RuntimeError("WINDOWS_STARTUP_TIMEOUT")

            assert get(address + "/p0/issues/K-ITR-1")[0] == 200
            assert get(address + "/p0/cases")[0] == 200
            request = urllib.request.Request(
                address + "/api/v2/issues/K-ITR-1/repeat-risk/queries",
                data=json.dumps({"include_missed_test": True, "top_k": 5}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.load(response)["result"]
            assert result["result_status"] == "READY_FOR_REVIEW"
            assert result["candidates"][0]["evidence"]
            print("WINDOWS_STARTUP=PASS")
        finally:
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    raise RuntimeError("WINDOWS_SHUTDOWN_TIMEOUT")

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    if "WinError 32" in log_text or "PermissionError" in log_text:
        raise RuntimeError("WINDOWS_SQLITE_HANDLE_ERROR")
    db = package / "data/runtime/quality_capability_p0.sqlite3"
    assert db.is_file()
    probe = db.with_suffix(".sqlite3.probe")
    os.replace(db, probe)
    os.replace(probe, db)
    print("WINDOWS_SHUTDOWN_NO_WINERROR32=PASS")
    print(f"WINDOWS_LOG={log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
