from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import socket
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def get(url: str, *, timeout: float = 5.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        return 0, b""


def wait_ready(base: str, process: subprocess.Popen, seconds: int = 90) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        code, _ = get(base + "/issues", timeout=2.0)
        if code == 200:
            return
        if process.poll() is not None:
            raise RuntimeError(f"launcher exited early with {process.returncode}")
        time.sleep(1)
    raise RuntimeError("launcher did not become ready")


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=["windows", "macos"], required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    temp_root = Path(tempfile.mkdtemp(prefix="step1b-qsv1-"))
    env = os.environ.copy()
    env["REPEAT_CASE_NO_PAUSE"] = "1"
    env["LEGACY_QUALITY_ISSUE_DB_PATH"] = str(temp_root / "legacy.db")
    env["QUALITY_SCENARIO_V1_DB_PATH"] = str(temp_root / "qsv1.db")

    if args.platform == "windows":
        command = [
            "cmd.exe",
            "/d",
            "/s",
            "/c",
            f"start_quality_capability_p1.bat --host 127.0.0.1 --port {args.port}",
        ]
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        launcher = ROOT / "start_quality_capability_p1.command"
        raw = launcher.read_bytes()
        if b"\r\n" in raw:
            raise RuntimeError("MAC_COMMAND_CRLF")
        if not os.access(launcher, os.X_OK):
            raise RuntimeError("MAC_COMMAND_NOT_EXECUTABLE")
        subprocess.run(["zsh", "-n", str(launcher)], check=True)
        command = [str(launcher), "--host", "127.0.0.1", "--port", str(args.port)]
        creationflags = 0

    log_path = temp_root / "launcher.log"
    log_handle = log_path.open("w+", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creationflags,
    )
    base = f"http://127.0.0.1:{args.port}"
    try:
        wait_ready(base, process)

        routes = (
            "/issues",
            "/analysis",
            "/import",
            "/itr/recovery-workbench",
            "/itr/resolution-workbench",
            "/software-assessment",
            "/missed-test-analysis",
            "/product-reports",
            "/quality-scenarios",
            "/quality-scenario-assets",
            "/quality-scenario-assets/portrait",
            "/quality-scenarios/insights",
            "/settings/scenario-taxonomy",
            "/p0/quality-scenarios/workbench",
            "/p0/quality-scenarios",
            "/p0/quality-scenarios/library/QSV1-SMOKE-DETAIL",
            "/p0/quality-scenario-insights",
        )
        for route in routes:
            code, body = get(base + route)
            if code != 200:
                raise RuntimeError(f"{route} => {code}")
            if route == "/issues" and "新质量场景（预览）".encode("utf-8") not in body:
                raise RuntimeError("MATURE_SIDEBAR_PREVIEW_ENTRY_MISSING")

        code, body = get(base + "/api/v2/quality-scenario-preview/status")
        if code != 200:
            raise RuntimeError(f"preview status => {code}")
        status = json.loads(body.decode("utf-8"))
        expected = {
            "V1_CANDIDATE_COUNT": 0,
            "V1_PUBLISHED_COUNT": 0,
            "P04_PROVIDER_TYPE": "QualityScenarioV1P04Provider",
            "P04_PUBLISHED_COUNT": 0,
            "SYNTHETIC_FIXTURE_USED": "NO",
        }
        if Path(status["V1_DB_ABSOLUTE_PATH"]).resolve() != (temp_root / "qsv1.db").resolve():
            raise RuntimeError("QSV1_DATABASE_BINDING_MISMATCH")
        for key, value in expected.items():
            if status.get(key) != value:
                raise RuntimeError(f"{key}={status.get(key)!r}, expected {value!r}")

        print(f"{args.platform.upper()}_START=PASS")
        print("MATURE_PLATFORM_BASE=PASS")
        print("NEW_QS_WORKBENCH_VISIBLE=PASS")
        print("NEW_QS_LIBRARY_VISIBLE=PASS")
        print("MATURE_ROUTES=PASS")
        print("QSV1_DETAIL_ROUTE=PASS")
        print("P04_REAL_PROVIDER_BINDING=PASS")
        print("SYNTHETIC_FIXTURE_USED=NO")
        print("V1_DB_ABSOLUTE_PATH=" + str(status["V1_DB_ABSOLUTE_PATH"]))
        print("V1_CANDIDATE_COUNT=0")
        print("V1_PUBLISHED_COUNT=0")
        print("P04_PUBLISHED_COUNT=0")
        return 0
    finally:
        stop_process(process)
        log_handle.flush()
        log_handle.seek(0)
        tail = log_handle.read()
        log_handle.close()
        if process.returncode not in (None, 0) and tail:
            print(tail[-12000:], file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
