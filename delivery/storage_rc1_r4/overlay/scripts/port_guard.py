from __future__ import annotations

import argparse
import errno
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


EXIT_PORT_CONFLICT = 98
EXIT_OWNERSHIP_FAILURE = 99
EXIT_OWNERSHIP_UNAVAILABLE = 97


def _print_kv(kind: str, **values: object) -> None:
    print(kind)
    for key, value in values.items():
        print(f"{key}={value}")


def _bind_probe(host: str, port: int) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    # Match normal server bind semantics: TIME_WAIT must not be treated as a
    # live port owner, while an actual LISTEN socket still rejects this bind.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    finally:
        sock.close()


def _listener_probe(host: str, port: int) -> str | None:
    # A bind-only probe is not portable enough for wildcard addresses:
    # macOS can allow a SO_REUSEADDR bind to 0.0.0.0 while an existing
    # loopback listener still owns 127.0.0.1:<port>. Probe for an actual
    # listener first, then retain the bind probe for non-accepting conflicts.
    if host in {"0.0.0.0", ""}:
        targets = ("127.0.0.1",)
    elif host == "::":
        targets = ("::1",)
    else:
        targets = (host,)

    for target in targets:
        family = socket.AF_INET6 if ":" in target else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(0.25)
        try:
            if sock.connect_ex((target, port)) == 0:
                return target
        finally:
            sock.close()
    return None


def check_free(host: str, port: int, service: str) -> int:
    listener_host = _listener_probe(host, port)
    if listener_host is not None:
        _print_kv(
            "PORT_CONFLICT",
            PORT=port,
            SERVICE=service,
            HOST=host,
            DETECTED_BY="LISTENER_CONNECT",
            PROBE_HOST=listener_host,
        )
        return EXIT_PORT_CONFLICT

    try:
        _bind_probe(host, port)
    except OSError as exc:
        if exc.errno in {errno.EADDRINUSE, 48, 98, 10048}:
            _print_kv(
                "PORT_CONFLICT",
                PORT=port,
                SERVICE=service,
                HOST=host,
                DETECTED_BY="BIND",
            )
            return EXIT_PORT_CONFLICT
        _print_kv(
            "PORT_CHECK_FAILED",
            PORT=port,
            SERVICE=service,
            HOST=host,
            ERROR=f"{type(exc).__name__}:{exc}",
        )
        return EXIT_OWNERSHIP_FAILURE
    _print_kv("PORT_FREE", PORT=port, SERVICE=service, HOST=host)
    return 0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _linux_pid_socket_inodes(pid: int) -> set[str]:
    fd_root = Path(f"/proc/{pid}/fd")
    if not fd_root.is_dir():
        return set()
    inodes: set[str] = set()
    for fd in fd_root.iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            inodes.add(target[8:-1])
    return inodes


def _linux_listen_inodes(port: int) -> set[str]:
    target_hex = f"{port:04X}"
    found: set[str] = set()
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            cols = line.split()
            if len(cols) < 10:
                continue
            local_addr = cols[1]
            state = cols[3]
            inode = cols[9]
            try:
                local_port = local_addr.rsplit(":", 1)[1].upper()
            except IndexError:
                continue
            if state == "0A" and local_port == target_hex:
                found.add(inode)
    return found


def _pid_owns_port_linux(pid: int, port: int) -> bool:
    return bool(_linux_pid_socket_inodes(pid) & _linux_listen_inodes(port))


def _pid_owns_port_macos(pid: int, port: int) -> bool | None:
    lsof = shutil.which("lsof") or ("/usr/sbin/lsof" if Path("/usr/sbin/lsof").is_file() else None)
    if not lsof:
        return None
    proc = subprocess.run(
        [lsof, "-nP", "-a", "-p", str(pid), f"-iTCP:{port}", "-sTCP:LISTEN"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def pid_owns_port(pid: int, port: int) -> bool | None:
    system = platform.system()
    if system == "Linux":
        return _pid_owns_port_linux(pid, port)
    if system == "Darwin":
        return _pid_owns_port_macos(pid, port)
    return None


def _health_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            response.read(64)
            return 200 <= int(getattr(response, "status", 200)) < 400
    except Exception:
        return False


def _tail(path: str | None, lines: int = 40) -> None:
    if not path:
        return
    p = Path(path)
    if not p.is_file():
        return
    try:
        data = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    print(f"LOG_TAIL={p}")
    for line in data[-lines:]:
        print(line)


def wait_owned(
    url: str,
    service: str,
    port: int,
    pid: int,
    timeout: float,
    interval: float,
    log_path: str | None,
) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            _print_kv(
                "SERVICE_START_FAILED",
                SERVICE=service,
                PORT=port,
                PID=pid,
                REASON="SPAWNED_PROCESS_EXITED",
            )
            _tail(log_path)
            return EXIT_OWNERSHIP_FAILURE

        if _health_ok(url):
            owns = pid_owns_port(pid, port)
            if owns is None:
                _print_kv(
                    "PORT_OWNERSHIP_CHECK_UNAVAILABLE",
                    SERVICE=service,
                    PORT=port,
                    PID=pid,
                    PLATFORM=platform.system(),
                )
                return EXIT_OWNERSHIP_UNAVAILABLE
            if not owns:
                _print_kv(
                    "HEALTH_OWNERSHIP_MISMATCH",
                    SERVICE=service,
                    PORT=port,
                    PID=pid,
                    URL=url,
                )
                _tail(log_path)
                return EXIT_OWNERSHIP_FAILURE
            _print_kv(
                "SERVICE_READY",
                SERVICE=service,
                PORT=port,
                PID=pid,
                URL=url,
                PID_ALIVE="PASS",
                PID_PORT_OWNERSHIP="PASS",
                HEALTH="PASS",
            )
            return 0

        time.sleep(interval)

    _print_kv(
        "SERVICE_START_TIMEOUT",
        SERVICE=service,
        PORT=port,
        PID=pid,
        URL=url,
        TIMEOUT_SECONDS=timeout,
    )
    _tail(log_path)
    return EXIT_OWNERSHIP_FAILURE


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    free = sub.add_parser("check-free")
    free.add_argument("--host", required=True)
    free.add_argument("--port", required=True, type=int)
    free.add_argument("--service", required=True)

    owned = sub.add_parser("wait-owned")
    owned.add_argument("--url", required=True)
    owned.add_argument("--service", required=True)
    owned.add_argument("--port", required=True, type=int)
    owned.add_argument("--pid", required=True, type=int)
    owned.add_argument("--timeout", type=float, default=20.0)
    owned.add_argument("--interval", type=float, default=0.25)
    owned.add_argument("--log")

    args = parser.parse_args()
    if args.command == "check-free":
        return check_free(args.host, args.port, args.service)
    if args.command == "wait-owned":
        return wait_owned(
            args.url,
            args.service,
            args.port,
            args.pid,
            args.timeout,
            args.interval,
            args.log,
        )
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
