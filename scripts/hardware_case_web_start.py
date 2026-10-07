"""Standalone Hardware Case internal-test Web launcher.

This is not a second Web application. It initializes the same Quality Capability
P0/P1 database and starts the same create_p0_app() FastAPI application, but it
avoids importing the repository-wide legacy CLI entrypoint (main.py), whose
top-level imports include unrelated Repeat Case builder modules.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.hardware_data_root import (
    HardwareDataRootResolution,
    HardwareDataRootResolver,
)
from services.hardware_startup_coordinator import HardwareStartupCoordinator


def build_app(*, data_root: str | Path, startup_status: dict[str, object]):
    from quality_knowledge.web import create_p0_app

    root = Path(data_root).expanduser().resolve(strict=False)
    hardware_db = root / "db" / "hardware_case_mvp.db"

    return create_p0_app(
        hardware_db,
        stage_runner=object(),
        project_root=ROOT,
        hardware_case_db_path=hardware_db,
        hardware_tree_upload_dir=root / "sources" / "tree_uploads",
        hardware_case_source_root=root / "sources",
        hardware_r1_workbench_db_path=root / "db" / "workbench_runtime.db",
        hardware_r1_preview_db_path=root / "rebuildable" / "preview.db",
        portrait_db_path=":memory:",
        hardware_startup_status=startup_status,
        enabled_domains={"HARDWARE_CASE"},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start the Hardware Case internal-test Web on the unified P0 app."
    )
    parser.add_argument(
        "--db",
        default=None,
    )
    parser.add_argument(
        "--hardware-db",
        default=None,
    )
    parser.add_argument(
        "--tree-upload-dir",
        default=None,
    )
    parser.add_argument(
        "--source-root",
        default=None,
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Build the app on an isolated temporary data root without listening.",
    )
    return parser


def _test_data_root_resolution(test_root: Path) -> HardwareDataRootResolution:
    """Classify an isolated root before a packaged startup smoke."""
    bootstrap_path = test_root.parent / f"{test_root.name}-bootstrap.json"
    result = HardwareDataRootResolver(
        ROOT,
        environment={"HARDWARE_DATA_ROOT": str(test_root)},
        bootstrap_path=bootstrap_path,
        legacy_roots=(),
    ).resolve()
    return result


def _run_isolated_startup_check() -> int:
    with tempfile.TemporaryDirectory(prefix="hardware-startup-check-") as temp:
        temp_root = Path(temp)
        resolution = _test_data_root_resolution(temp_root)
        print("DATA_ROOT_CLASSIFICATION=" + resolution.classification)
        if resolution.classification != "FIRST_INSTALL":
            print("RESULT=BLOCKED")
            print("BLOCKER=" + (resolution.error_code or "DATA_ROOT_TEST_CLASSIFICATION_FAILED"))
            return 3
        resolver = HardwareDataRootResolver(
            ROOT,
            environment={"HARDWARE_DATA_ROOT": str(temp_root)},
            bootstrap_path=temp_root.parent / f"{temp_root.name}-bootstrap.json",
            legacy_roots=(),
        )
        startup_status = HardwareStartupCoordinator(
            ROOT, resolver=resolver, resolution=resolution
        ).run()
        print("STARTUP_STATUS=" + str(startup_status.get("status")))
        if not startup_status.get("ready"):
            print("RESULT=BLOCKED")
            print("BLOCKER=" + str(startup_status.get("error_code") or "HARDWARE_STARTUP_NOT_READY"))
            return 3
        app = build_app(data_root=temp_root, startup_status=startup_status)
        try:
            p01_path = str(app.url_path_for("hardware_case_home"))
            p07_path = str(app.url_path_for("hardware_case_base_data"))
            import_path = str(app.url_path_for("list_imports"))
        except Exception as exc:
            print("RESULT=BLOCKED")
            print("ROUTE_RESOLUTION_FAILED=" + type(exc).__name__)
            status = getattr(app.state, "initialization_status", None)
            if status is not None:
                import json

                print("INITIALIZATION_STATUS=" + json.dumps(status, ensure_ascii=False, default=str))
            return 3

        print("RESULT=PASS")
        print("STARTUP_IMPORT=PASS")
        print("APP_FACTORY=create_p0_app")
        print("ENABLED_DOMAINS=HARDWARE_CASE")
        print("WEB_ENTRY=" + p01_path)
        print("BASE_DATA_ENTRY=" + p07_path)
        print("TREE_IMPORT_ENTRY=" + import_path)
        return 0


def main() -> int:
    args = build_parser().parse_args()

    if args.check:
        return _run_isolated_startup_check()

    configured_bootstrap = str(os.getenv("HARDWARE_BOOTSTRAP_PATH") or "").strip()
    resolver = HardwareDataRootResolver(
        ROOT,
        bootstrap_path=configured_bootstrap or None,
    )
    resolution = resolver.resolve()
    print("DATA_ROOT_CLASSIFICATION=" + resolution.classification)
    startup_status = HardwareStartupCoordinator(
        ROOT, resolver=resolver, resolution=resolution
    ).run()
    print("STARTUP_STATUS=" + str(startup_status.get("status")))
    print("STARTUP_PHASE=" + str(startup_status.get("phase")))
    print("READY=" + str(bool(startup_status.get("ready"))).upper())
    if startup_status.get("error_code"):
        print("BLOCKER=" + str(startup_status["error_code"]))
    data_root = startup_status.get("data_root") or resolution.data_root or resolver.default_data_root
    app = build_app(data_root=data_root, startup_status=startup_status)
    if startup_status.get("ready"):
        print("RESULT=READY")
    else:
        print("RESULT=BLOCKED_DIAGNOSTICS_ONLY")
    try:
        import uvicorn
    except ImportError:
        print("BLOCKER=UVICORN_UNAVAILABLE")
        return 3
    uvicorn.run(app, host=args.host, port=args.port)
    return 0 if startup_status.get("ready") else 3


if __name__ == "__main__":
    raise SystemExit(main())
