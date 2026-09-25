from __future__ import annotations

import argparse
import sys
import time
import urllib.request


def main() -> int:
    p = argparse.ArgumentParser(description='Wait for an HTTP endpoint to become reachable.')
    p.add_argument('url')
    p.add_argument('--name', default='service')
    p.add_argument('--timeout', type=float, default=20.0)
    p.add_argument('--interval', type=float, default=0.25)
    args = p.parse_args()
    deadline = time.monotonic() + args.timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(args.url, timeout=1.0) as resp:
                if 200 <= resp.status < 500:
                    return 0
        except Exception as exc:  # pragma: no cover - helper script
            last = exc
        time.sleep(args.interval)
    print(f'ERROR {args.name} health check failed: {args.url}; last={last}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
