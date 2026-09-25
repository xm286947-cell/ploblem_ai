from __future__ import annotations

import argparse
import os
import socket
import ssl
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import yaml


def safe_proxy(value: str) -> str:
    parsed = urlsplit(str(value or ""))
    if not parsed.scheme or not parsed.hostname:
        return "<set>"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _recv_status_line(sock: socket.socket) -> str:
    data = b""
    while b"\n" not in data and len(data) < 1024:
        chunk = sock.recv(256)
        if not chunk:
            break
        data += chunk
    if not data:
        return "<no response bytes>"
    return data.splitlines()[0].decode("iso-8859-1", errors="replace")[:300]


def _protocol_probe(host: str, port: int, scheme: str, path: str) -> None:
    probe_path = (path.rstrip("/") if path else "") + "/models"
    if not probe_path.startswith("/"):
        probe_path = "/" + probe_path
    request = (
        f"GET {probe_path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Accept: application/json\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii", errors="ignore")

    raw = socket.create_connection((host, port), timeout=8)
    try:
        conn: socket.socket = raw
        if scheme == "https":
            ctx = ssl.create_default_context()
            conn = ctx.wrap_socket(raw, server_hostname=host)
            print("  tls=PASS", conn.version(), conn.cipher()[0])
            cert = conn.getpeercert()
            print("  tls_peer_subject=", cert.get("subject", "<unavailable>"))
        else:
            print("  tls=SKIP_HTTP")
        conn.sendall(request)
        status_line = _recv_status_line(conn)
        print("  http_protocol_status_line=", status_line)
        if status_line.startswith("HTTP/"):
            print("  http_protocol=PASS")
        else:
            print("  http_protocol=FAIL_NON_HTTP_RESPONSE")
    finally:
        try:
            raw.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-config", required=True)
    args = ap.parse_args()

    path = Path(args.model_config).expanduser().resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models = raw.get("models") or {}
    active = str(raw.get("active_model") or "qwen_prod")
    profile = models.get(active) or models.get("qwen_prod") or {}
    base = str(profile.get("base_url") or "").strip()
    if not base and profile.get("base_url_env"):
        base = os.environ.get(str(profile["base_url_env"]), "").strip()

    parsed = urlsplit(base)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    final = base.rstrip("/") + (
        "" if base.rstrip("/").endswith("/chat/completions") else "/chat/completions"
    )

    print("PROVIDER_NETWORK_PROBE")
    print("  model_config=", path)
    print("  active_model=", active)
    print("  model=", profile.get("model"))
    print("  scheme=", parsed.scheme or "<missing>")
    print("  host=", host or "<missing>")
    print("  port=", port)
    print("  base_path=", parsed.path or "/")
    print("  final_url=", final)
    print("  api_key_present=", bool(profile.get("api_key")))

    proxies = getproxies()
    print("  proxy_bypass=", proxy_bypass(host) if host else False)
    for key in ("http", "https", "all"):
        if key in proxies:
            print(f"  proxy_{key}=", safe_proxy(proxies[key]))

    if not host:
        print("  dns=SKIP_NO_HOST")
        return 2

    try:
        addrs = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = sorted({item[4][0] for item in addrs})
        print("  dns=PASS", ",".join(ips[:6]))
    except Exception as exc:
        print("  dns=FAIL", type(exc).__name__, str(exc)[:180])
        return 2

    try:
        with socket.create_connection((host, port), timeout=8):
            print("  tcp=PASS")
    except Exception as exc:
        print("  tcp=FAIL", type(exc).__name__, str(exc)[:240])
        return 2

    # This probe talks directly to the configured host:port with a minimal
    # standards-compliant HTTP request. 401/404/405 are fine: the point is to
    # prove whether the socket actually speaks HTTP vs HTTPS/TLS.
    try:
        _protocol_probe(host, port, parsed.scheme, parsed.path)
    except Exception as exc:
        print("  http_protocol=FAIL", type(exc).__name__, str(exc)[:240])
        # Do not block product execution solely because /models probing is
        # unsupported. The full Runtime Provider request remains authoritative.

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
