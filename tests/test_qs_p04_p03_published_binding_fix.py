from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p04.fixtures import FixtureP04Provider
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path) -> TestClient:
    db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    return TestClient(
        create_p0_app(
            db,
            stage_runner=object(),
            p04_provider=FixtureP04Provider(),
            hardware_case_db_path=tmp_path / "hardware.sqlite3",
            hardware_tree_upload_dir=tmp_path / "tree_uploads",
            hardware_case_source_root=tmp_path / "sources",
        )
    )


def test_p04_published_identity_resolves_to_p03_evidence_and_source(tmp_path: Path) -> None:
    client = _client(tmp_path)
    query = client.post(
        "/api/v2/quality-scenario-insights/v1/query", json={"view": "PRODUCT"}
    )
    assert query.status_code == 200
    item = next(
        entry for entry in query.json()["scenario_list"] if entry["scenario_id"] == "QS-FIX-002"
    )
    scenario_id = item["scenario_id"]

    p03 = client.get(f"/p0/quality-scenarios/{scenario_id}")
    compatibility = client.get(item["p03_path"])
    assert p03.status_code == 200
    assert compatibility.status_code == 200
    assert scenario_id in p03.text
    assert "Evidence / 证据与来源" in p03.text
    assert "PROBLEM-002" in p03.text
    assert "Source Reference" in p03.text

    detail_api = client.get(f"/api/v2/quality-scenarios/{scenario_id}")
    insight_detail_api = client.get(
        f"/api/v2/quality-scenario-insights/v1/scenarios/{scenario_id}"
    )
    assert detail_api.status_code == 200
    assert insight_detail_api.status_code == 200
    assert detail_api.json()["scenario_id"] == item["scenario_id"] == insight_detail_api.json()["scenario_id"]
    assert detail_api.json()["status"] == "PUBLISHED"
    assert detail_api.json()["evidence_refs"]

    source_ref = detail_api.json()["source_problem_refs"][0]["source_ref"]
    source = client.get(
        "/api/v2/quality-scenario-insights/v1/sources/" + source_ref
    )
    assert source.status_code == 200
    assert source.json()["source_ref"] == source_ref
    assert source.json()["items"][0]["scenario_id"] == scenario_id


def test_p03_missing_scenario_is_fail_closed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/p0/quality-scenarios/QS-NOT-FOUND").status_code == 404
    assert client.get("/api/v2/quality-scenarios/QS-NOT-FOUND").status_code == 404
    assert client.get("/api/v2/quality-scenario-insights/v1/scenarios/QS-NOT-FOUND").status_code == 404
    assert client.get("/api/v2/quality-scenario-insights/v1/sources/UNKNOWN").status_code == 404
