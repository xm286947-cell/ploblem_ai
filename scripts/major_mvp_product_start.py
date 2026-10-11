"""Start the Major MVP product profile on the existing unified P0 Web host."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_app(data_root: str | Path):
    """Initialize candidate-owned data and compose the existing product host."""
    from quality_knowledge.p0.initializer import P0Initializer
    from quality_knowledge.web.p0_app import create_p0_app

    root = Path(data_root).expanduser().resolve()
    quality_root = root / "quality"
    major_root = root / "major"
    historical_root = root / "historical_case"
    for directory in (
        quality_root / "db",
        major_root / "db",
        major_root / "attachments",
        historical_root,
        root / "logs",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    p0_db = quality_root / "db" / "quality_capability_p0.db"
    initializer = P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )
    if p0_db.exists():
        initializer.verify_ready(p0_db)
    else:
        initializer.initialize(p0_db)

    major_db = major_root / "db" / "major_case.db"
    return create_p0_app(
        p0_db,
        project_root=ROOT,
        major_case_db_path=major_db,
        major_attachment_root=major_root / "attachments",
        major_artifact_root=historical_root,
        portrait_db_path=quality_root / "db" / "portrait.db",
        repeat_runtime_data_root=root,
        enabled_domains={"QUALITY_ISSUE", "REPEAT_RISK"},
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start Major MVP on the existing Quality Capability P0 host."
    )
    parser.add_argument(
        "--data-root",
        default=os.getenv("MAJOR_MVP_DATA_ROOT") or str(ROOT / "data"),
        help="Candidate-owned writable data directory (default: ./data).",
    )
    parser.add_argument("--host", default=os.getenv("MAJOR_MVP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MAJOR_MVP_PORT", "8080")))
    parser.add_argument(
        "--check",
        action="store_true",
        help="Initialize isolated temporary data and verify the unified app routes without listening.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.check:
            with tempfile.TemporaryDirectory(prefix="major-mvp-start-check-") as temp:
                app = build_app(temp)
                from fastapi.testclient import TestClient

                client = TestClient(app)
                probes = {
                    "/p0/major-production": 200,
                    "/p0/cases": 200,
                    "/p0/cases/CANDIDATE-START-CHECK": 200,
                    "/p0/issues": 200,
                    "/p0/issues/CANDIDATE-START-CHECK": 200,
                    "/api/v2/major-production/excel/template": 200,
                    "/api/v2/historical-cases": 200,
                }
                failed = {
                    path: (response.status_code, expected)
                    for path, expected in probes.items()
                    if (response := client.get(path)).status_code != expected
                }
                api_paths = app.openapi().get("paths", {})
                repeat_route_present = "/api/v2/issues/{knowledge_id}/repeat-risk" in api_paths
                if not repeat_route_present:
                    failed["/api/v2/issues/{knowledge_id}/repeat-risk"] = (404, 200)
                print("APP_FACTORY=create_p0_app")
                print("ENABLED_DOMAINS=QUALITY_ISSUE,REPEAT_RISK")
                print("ROUTE_CHECK=" + ("PASS" if not failed else "FAIL"))
                if failed:
                    print("FAILED_ROUTES=" + json.dumps(failed, ensure_ascii=False))
                    return 2
            return 0

        import uvicorn

        data_root = Path(args.data_root).expanduser().resolve()
        app = build_app(data_root)
        display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
        base_url = f"http://{display_host}:{args.port}"
        print(f"PRODUCT_HOST=create_p0_app")
        print(f"ENABLED_DOMAINS=QUALITY_ISSUE,REPEAT_RISK")
        print(f"DATA_ROOT={data_root}")
        print(f"BASE_URL={base_url}")
        print(f"Major Production: {base_url}/p0/major-production")
        print(f"Historical Cases: {base_url}/p0/cases")
        print(f"Issues / Repeat Risk: {base_url}/p0/issues")
        uvicorn.run(app, host=args.host, port=args.port)
        return 0
    except Exception as error:
        detail = {
            "result": "STARTUP_BLOCKED",
            "error_type": type(error).__name__,
            "error": str(error),
        }
        print(json.dumps(detail, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
