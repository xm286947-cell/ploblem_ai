"""Standalone Hardware Case internal-test Web launcher.

This is not a second Web application. It initializes the same Quality Capability
P0/P1 database and starts the same create_p0_app() FastAPI application, but it
avoids importing the repository-wide legacy CLI entrypoint (main.py), whose
top-level imports include unrelated Repeat Case builder modules.
"""
from __future__ import annotations

import argparse
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


def build_app(
    *,
    db_path: str | Path,
    hardware_case_db_path: str | Path,
    hardware_tree_upload_dir: str | Path,
    hardware_case_source_root: str | Path,
    data_root_resolution: HardwareDataRootResolution,
):
    if data_root_resolution.classification not in {"FIRST_INSTALL", "EXISTING_INSTALL"}:
        raise RuntimeError(
            data_root_resolution.error_code or "HARDWARE_DATA_ROOT_NOT_READY"
        )
    from quality_knowledge.web import create_p0_app

    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)

    hardware_db = Path(hardware_case_db_path)
    hardware_db.parent.mkdir(parents=True, exist_ok=True)
    upload_dir = Path(hardware_tree_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    return create_p0_app(
        db,
        stage_runner=object(),
        project_root=ROOT,
        hardware_case_db_path=hardware_db,
        hardware_tree_upload_dir=upload_dir,
        hardware_case_source_root=hardware_case_source_root,
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

        app = build_app(
            db_path=temp_root / "quality_capability_p1.db",
            hardware_case_db_path=temp_root / "hardware_case_mvp.db",
            hardware_tree_upload_dir=temp_root / "tree_uploads",
            hardware_case_source_root=temp_root / "sources",
            data_root_resolution=resolution,
        )
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

    resolution = HardwareDataRootResolver(ROOT).resolve()
    print("DATA_ROOT_CLASSIFICATION=" + resolution.classification)
    if resolution.classification in {"BLOCKED", "LEGACY_UPGRADE_REQUIRED"}:
        print("RESULT=BLOCKED")
        print("BLOCKER=" + (resolution.error_code or resolution.classification))
        return 3
    # The startup coordinator (A4) owns first-install initialization and
    # existing-install readiness checks. Until it is present, this launcher
    # must not construct any store.
    print("RESULT=BLOCKED")
    print("BLOCKER=HARDWARE_STARTUP_COORDINATOR_REQUIRED")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
