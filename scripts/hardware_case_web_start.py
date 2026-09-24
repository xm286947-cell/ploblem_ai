"""Standalone Hardware Case internal-test Web launcher.

This is not a second Web application. It initializes the same Quality Capability
P0/P1 database and starts the same create_p0_app() FastAPI application, but it
avoids importing the repository-wide legacy CLI entrypoint (main.py), whose
top-level imports include unrelated Repeat Case builder modules.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web import create_p0_app


def build_app(
    *,
    db_path: str | Path,
    hardware_case_db_path: str | Path,
    hardware_tree_upload_dir: str | Path,
):
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    initializer = P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )
    if db.exists():
        initializer.verify_ready(db)
    else:
        initializer.initialize(db)

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
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start the Hardware Case internal-test Web on the unified P0 app."
    )
    parser.add_argument(
        "--db",
        default=str(ROOT / "data/quality_capability_p1.db"),
    )
    parser.add_argument(
        "--hardware-db",
        default=str(ROOT / "data/hardware_case_mvp.db"),
    )
    parser.add_argument(
        "--tree-upload-dir",
        default=str(ROOT / "data/hardware_case_tree_uploads"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Initialize and build the app without opening a listening socket.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    app = build_app(
        db_path=args.db,
        hardware_case_db_path=args.hardware_db,
        hardware_tree_upload_dir=args.tree_upload_dir,
    )

    try:
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

    if args.check:
        print("RESULT=PASS")
        print("STARTUP_IMPORT=PASS")
        print("APP_FACTORY=create_p0_app")
        print("WEB_ENTRY=" + p07_path)
        print("TREE_IMPORT_ENTRY=" + import_path)
        return 0

    import uvicorn

    print("Hardware Case Product Test")
    print("P07: http://127.0.0.1:%s/p0/hardware-cases/base-data" % args.port)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
