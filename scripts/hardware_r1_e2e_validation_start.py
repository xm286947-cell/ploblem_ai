"""Start the existing Hardware Case Web in an isolated E2E data profile."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.hardware_data_root import HardwareDataRootResolver
from services.hardware_startup_coordinator import HardwareStartupCoordinator
from scripts.hardware_case_web_start import build_app
from fastapi.testclient import TestClient


def _default_base() -> Path:
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "HardwareR1E2E"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/HardwareR1E2E"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "HardwareR1E2E"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=os.getenv("HARDWARE_R1_E2E_DATA_ROOT"))
    parser.add_argument("--bootstrap", default=os.getenv("HARDWARE_R1_E2E_BOOTSTRAP_PATH"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--check", action="store_true", help="Initialize and validate without starting Web.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the local Web page automatically.")
    return parser


def resolve_profile(args: argparse.Namespace):
    base = Path(args.data_root).expanduser().resolve().parent if args.data_root else _default_base()
    data_root = Path(args.data_root).expanduser().resolve() if args.data_root else base / "Data"
    bootstrap = Path(args.bootstrap).expanduser().resolve() if args.bootstrap else base / "bootstrap.json"
    resolver = HardwareDataRootResolver(
        ROOT,
        environment={**os.environ, "HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=bootstrap,
        legacy_roots=(),
    )
    normal_root = resolver.default_data_root
    if data_root == normal_root or data_root in normal_root.parents or normal_root in data_root.parents:
        raise ValueError("VALIDATION_DATA_ROOT_COLLISION")
    if data_root == ROOT or ROOT in data_root.parents or data_root in ROOT.parents:
        raise ValueError("VALIDATION_DATA_ROOT_OVERLAPS_APPLICATION")
    if bootstrap == data_root or data_root in bootstrap.parents:
        raise ValueError("VALIDATION_BOOTSTRAP_LOCATION_INVALID")
    return resolver, data_root, bootstrap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        resolver, data_root, bootstrap = resolve_profile(args)
    except ValueError as error:
        print(f"RESULT=BLOCKED\nBLOCKER={error}", file=sys.stderr)
        return 3
    os.environ["HARDWARE_DATA_ROOT"] = str(data_root)
    os.environ["HARDWARE_CASE_RUNTIME_DB"] = str(data_root / "audit/runtime/hardware_case_runtime.db")
    os.environ["HARDWARE_R1_E2E_PROFILE"] = "1"
    resolution = resolver.resolve()
    print(f"DATA_ROOT_CLASSIFICATION={resolution.classification}")
    print(f"E2E_DATA_ROOT={data_root}")
    print(f"NORMAL_PRODUCT_DATA_ROOT={resolver.default_data_root}")
    print("LEGACY_ROOTS=DISABLED")
    if resolution.blocked:
        print(f"RESULT=BLOCKED\nBLOCKER={resolution.error_code}", file=sys.stderr)
        return 3
    startup = HardwareStartupCoordinator(ROOT, resolver=resolver, resolution=resolution).run()
    print(f"STARTUP_STATUS={startup.get('status')}")
    if not startup.get("ready"):
        print(f"RESULT=BLOCKED\nBLOCKER={startup.get('error_code') or 'HARDWARE_STARTUP_NOT_READY'}", file=sys.stderr)
        return 3
    if args.check:
        app = build_app(data_root=data_root, startup_status=startup)
        checks = {
            "E2E_LANDING": ("GET", "/p0/hardware-cases/e2e", 200, {}),
            "WORKBENCH_PAGE": ("GET", "/p0/hardware-cases/knowledge-production", 200, {}),
            "KNOWLEDGE_PAGE": ("GET", "/p0/hardware-cases/knowledge?e2e=1", 200, {}),
            "WORKBENCH_API": ("GET", "/api/v2/hardware-cases/r1/workbench/batches", 200, {"X-Hardware-Case-Role": "MAINTAINER"}),
            "CONSUMPTION_API": ("GET", "/api/public/hardware-knowledge/v1/search", 503, {}),
            "READINESS_API": ("GET", "/api/e2e/hardware-r1/readiness", 200, {}),
        }
        with TestClient(app) as client:
            for name, (method, path, expected, headers) in checks.items():
                response = client.request(method, path, headers=headers)
                if response.status_code != expected:
                    print(f"RESULT=BLOCKED\nCHECK={name}\nEXPECTED={expected}\nACTUAL={response.status_code}\nBODY={response.text[:500]}", file=sys.stderr)
                    return 3
                print(f"{name}=PASS")
        print("RESULT=PASS\nAPP_FACTORY=create_p0_app\nENABLED_DOMAINS=HARDWARE_CASE")
        return 0
    app = build_app(data_root=data_root, startup_status=startup)
    try:
        import uvicorn
    except ImportError:
        print("RESULT=BLOCKED\nBLOCKER=UVICORN_UNAVAILABLE", file=sys.stderr)
        return 3
    print("OPEN=http://127.0.0.1:%d/p0/hardware-cases/e2e" % args.port)
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/p0/hardware-cases/e2e")).start()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
