from __future__ import annotations

import argparse
import contextlib
import http.server
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


CONFLICT_CASES = (
    ("TEST-PORT-01", 8765, "Storage-Web"),
    ("TEST-PORT-02", 18000, "OpenAI-Mock"),
    ("TEST-PORT-03", 18001, "Storage-Mock-Router"),
)


@contextlib.contextmanager
def occupied_port(port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(4)
    try:
        yield
    finally:
        sock.close()


def _clean_e2e_results(root: Path) -> None:
    for name in ("PRODUCT_E2E_RESULT.json", "PRODUCT_FAILURE_E2E_RESULT.json"):
        with contextlib.suppress(FileNotFoundError):
            (root / "release" / name).unlink()


def _run_launcher(root: Path, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["STORAGE_TEST_NO_WAIT"] = "1"
    env["STORAGE_TEST_RESET_DATA"] = "1"
    env["STORAGE_APP_LOG_STDOUT"] = "0"
    return subprocess.run(
        ["bash", "start_test.sh", "mock"],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_conflict(root: Path, test_id: str, port: int, service: str) -> None:
    _clean_e2e_results(root)
    with occupied_port(port):
        proc = _run_launcher(root)
    out = proc.stdout
    expected = (
        "PORT_CONFLICT",
        f"PORT={port}",
        f"SERVICE={service}",
        "PRODUCT_E2E=NOT_RUN",
    )
    if proc.returncode != 98 or any(token not in out for token in expected):
        raise SystemExit(
            f"{test_id}=FAIL rc={proc.returncode} expected={expected}\n{out[-4000:]}"
        )
    if "PRODUCT E2E PASS" in out or (root / "release" / "PRODUCT_E2E_RESULT.json").exists():
        raise SystemExit(f"{test_id}=FAIL product_e2e_ran")
    print(f"{test_id}=PASS PORT={port} SERVICE={service} FAIL_FAST=PASS PRODUCT_E2E=NOT_RUN")


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, fmt: str, *args: object) -> None:
        return


def test_dead_pid_cannot_borrow_health(root: Path) -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        dead = subprocess.Popen(
            [sys.executable, "-c", "import time,sys; time.sleep(0.1); sys.exit(7)"]
        )
        dead.wait(timeout=5)
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/port_guard.py",
                "wait-owned",
                "--url",
                f"http://127.0.0.1:{port}/health",
                "--service",
                "Dead-Spawn-Probe",
                "--port",
                str(port),
                "--pid",
                str(dead.pid),
                "--timeout",
                "2",
                "--interval",
                "0.1",
            ],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 99 or "SPAWNED_PROCESS_EXITED" not in proc.stdout:
            raise SystemExit(
                f"TEST-PORT-05=FAIL rc={proc.returncode}\n{proc.stdout[-4000:]}"
            )
        print(
            "TEST-PORT-05=PASS DEAD_PID_REJECTED=PASS "
            "EXISTING_HEALTH_NOT_ACCEPTED=PASS"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, default=Path("."))
    parser.add_argument("--include-normal", action="store_true")
    args = parser.parse_args()
    root = args.package_root.resolve()
    if not (root / "start_test.sh").is_file():
        raise SystemExit(f"package root invalid: {root}")

    for test_id, port, service in CONFLICT_CASES:
        test_conflict(root, test_id, port, service)
    test_dead_pid_cannot_borrow_health(root)

    if args.include_normal:
        _clean_e2e_results(root)
        proc = _run_launcher(root, timeout=240)
        if proc.returncode != 0 or "PRODUCT E2E PASS" not in proc.stdout:
            raise SystemExit(
                f"TEST-PORT-04=FAIL rc={proc.returncode}\n{proc.stdout[-8000:]}"
            )
        print("TEST-PORT-04=PASS FREE_PORT_NORMAL_MOCK_E2E=PASS")
    else:
        print("TEST-PORT-04=DEFERRED_TO_SELFCHECK_MOCK_NORMAL")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
