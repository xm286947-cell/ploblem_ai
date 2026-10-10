"""Frozen M8.2/M8.3 report HTTP delivery remains reachable after Linux port (#614)."""
from pathlib import Path
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app

ROOT = Path(__file__).resolve().parents[1]


def test_repeat_markdown_report_route_is_real_and_preserves_content_type(tmp_path: Path):
    db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)

    class StubReport:
        def report(self, query_id: str, *, format: str = "markdown"):
            assert query_id == "RQ-FROZEN"
            assert format == "markdown"
            return {"content": "# Repeat evidence report\n", "content_type": "text/markdown"}

    app = create_p0_app(
        db, project_root=ROOT, stage_runner=object(),
        enabled_domains={"QUALITY_ISSUE", "REPEAT_RISK"},
        repeat_web=StubReport(),
    )
    assert "/api/v2/repeat-risk/queries/{query_id}/report" in app.openapi()["paths"]
    response = TestClient(app).get("/api/v2/repeat-risk/queries/RQ-FROZEN/report?format=markdown")
    assert response.status_code == 200, response.text
    assert response.text == "# Repeat evidence report\n"
    assert response.headers["content-type"].startswith("text/markdown")


def test_missing_repeat_report_is_404_not_silent_success(tmp_path: Path):
    db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)

    class StubReport:
        def report(self, query_id: str, *, format: str = "markdown"):
            raise ValueError("REPEAT_REPORT_NOT_FOUND")

    app = create_p0_app(
        db, project_root=ROOT, stage_runner=object(),
        enabled_domains={"QUALITY_ISSUE", "REPEAT_RISK"},
        repeat_web=StubReport(),
    )
    response = TestClient(app).get("/api/v2/repeat-risk/queries/RQ-MISSING/report")
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "REPEAT_REPORT_NOT_FOUND"
