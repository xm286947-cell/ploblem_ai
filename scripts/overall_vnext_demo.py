"""Run the Overall VNext MVP on the existing single P0 host.

The demo initializes only its own P0 and Hardware Case databases. Product
composition stays in create_p0_app, including the existing Storage mount.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p04.fixtures import FixtureP04Provider
from quality_knowledge.web.p0_app import create_p0_app


DEMO_LEGACY_ISSUE_ID = "ITR-VNEXT-DEMO-001"


def prepare_demo_legacy_database(data_dir: Path) -> Path:
    """Create an isolated Legacy store and one synthetic issue for the demo."""
    from quality_knowledge.web.app import create_legacy_quality_issue_router

    legacy_db = data_dir / "legacy_quality_issue.sqlite3"
    _, legacy_state = create_legacy_quality_issue_router(
        legacy_db,
        initialize_schema=True,
    )
    mapping_service = legacy_state.mapping_configuration_service
    if mapping_service.get_effective_config("PLC") is None:
        mapping_result = mapping_service.migrate_yaml(
            ROOT / "quality_knowledge/config/plc_fields.yaml",
            "PLC",
            dry_run=False,
        )
        if mapping_result.get("status") != "ACTIVE":
            raise RuntimeError("DEMO_LEGACY_MAPPING_NOT_ACTIVE")
    service = legacy_state.knowledge_issue_service
    if not service.query_issues({"business_issue_id": DEMO_LEGACY_ISSUE_ID}, limit=1):
        workbook_path = data_dir / "legacy_demo_seed.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["ITR单号", "问题描述", "产品", "月份"])
        sheet.append(
            [
                DEMO_LEGACY_ISSUE_ID,
                "合成问题：Overall VNext Legacy 工作台与分析链演示",
                "PLC",
                "2026-09",
            ]
        )
        workbook.save(workbook_path)
        try:
            service.import_file(workbook_path, "PLC")
        finally:
            workbook_path.unlink(missing_ok=True)
    return legacy_db


def build_app(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["STORAGE_LIFE_DATA_DIR"] = str(data_dir / "storage")
    os.environ["STORAGE_KNOWLEDGE_REPOSITORY_DIR"] = str(data_dir / "knowledge_repository")
    os.environ.pop("LEGACY_QUALITY_ISSUE_DB_PATH", None)
    p0_db = data_dir / "quality_capability_p0.sqlite3"
    hardware_db = data_dir / "hardware_case.sqlite3"
    initializer = P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )
    if p0_db.exists():
        initializer.verify_ready(p0_db)
    else:
        initializer.initialize(p0_db)
    legacy_db = prepare_demo_legacy_database(data_dir)
    app = create_p0_app(
        p0_db,
        project_root=ROOT,
        hardware_case_db_path=hardware_db,
        hardware_tree_upload_dir=data_dir / "hardware_tree_uploads",
        hardware_case_source_root=data_dir / "hardware_case_sources",
        p04_provider=FixtureP04Provider(result_revision="overall-vnext-demo-v1"),
        legacy_quality_issue_db_path=legacy_db,
    )
    if not app.state.overall_shell_enabled:
        raise RuntimeError("OVERALL_SHELL_NOT_ENABLED")
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "overall-vnext-fast-mvp",
        help="Isolated P0 and Hardware Case data directory (default: OS temp directory).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--check", action="store_true", help="Build app and resolve MVP routes without serving.")
    args = parser.parse_args()

    app = build_app(args.data_dir)
    if args.check:
        from fastapi.testclient import TestClient

        client = TestClient(app)
        for path in (
            "/p0/overall",
            "/p0/overall/areas/current-problem",
            "/p0/overall/areas/cases-knowledge",
            "/p0/overall/areas/scenarios-insights",
            "/p0/overall/areas/professional-topics",
            "/p0/overall/areas/management",
            "/p0/issues",
            "/issues",
            "/analysis",
            "/import",
            "/statistics",
            "/p0/quality-scenario-insights",
            "/p0/hardware-cases",
            "/storage-workspace/",
            "/storage-workspace/knowledge-production/published",
            "/storage-workspace/knowledge-production/sources",
        ):
            response = client.get(path)
        if response.status_code != 200:
            raise RuntimeError(f"MVP_ROUTE_FAILED:{path}:{response.status_code}")
        legacy_issues = app.state.legacy_quality_issue_services.knowledge_issue_service.query_issues(
            {"business_issue_id": DEMO_LEGACY_ISSUE_ID}, limit=1
        )
        if not legacy_issues:
            raise RuntimeError("MVP_LEGACY_FIXTURE_FAILED")
        legacy_detail = client.get(f"/issues/{legacy_issues[0]['knowledge_id']}")
        if legacy_detail.status_code != 200 or DEMO_LEGACY_ISSUE_ID not in legacy_detail.text:
            raise RuntimeError("MVP_LEGACY_DETAIL_FAILED")
        root = client.get("/", follow_redirects=False)
        if root.status_code not in {302, 307} or root.headers.get("location") != "/p0/issues":
            raise RuntimeError("MVP_DEFAULT_ENTRY_FAILED")
        knowledge_compatibility = client.get(
            "/p0/knowledge/hardware", follow_redirects=False
        )
        if (
            knowledge_compatibility.status_code not in {302, 307}
            or knowledge_compatibility.headers.get("location") != "/p0/hardware-cases"
        ):
            raise RuntimeError("MVP_KNOWLEDGE_COMPATIBILITY_FAILED")
        query = client.post(
            "/api/v2/quality-scenario-insights/v1/query",
            json={"view": "INDUSTRY"},
        )
        if query.status_code != 200:
            raise RuntimeError(f"MVP_P04_QUERY_FAILED:{query.status_code}")
        scenario_ids = {
            item["scenario_id"] for item in query.json().get("scenario_list", [])
        }
        if "QS-FIX-002" not in scenario_ids:
            raise RuntimeError("MVP_P04_FIXTURE_FAILED")
        detail = client.get(
            "/p0/quality-scenarios/QS-FIX-002?return_to=/p0/quality-scenario-insights"
        )
        source = client.get(
            "/p0/quality-scenario-sources/PROBLEM-003?return_to=/p0/quality-scenario-insights"
        )
        if (
            detail.status_code != 200
            or source.status_code != 200
            or "PROBLEM-003" not in source.text
        ):
            raise RuntimeError("MVP_P04_DETAIL_SOURCE_SMOKE_FAILED")

    print("APP_FACTORY=create_p0_app")
    print("OVERALL_SHELL=READY")
    print("P04_DEMO_DATA=SYNTHETIC_QS-FIX_FIXTURES")
    print(f"LEGACY_DEMO_DATA=SYNTHETIC_{DEMO_LEGACY_ISSUE_ID}")
    print(f"DATA_DIR={args.data_dir.resolve()}")
    print(f"WEB_URL=http://{args.host}:{args.port}/p0/overall")
    if args.check:
        print("RESULT=PASS")
        return 0

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
