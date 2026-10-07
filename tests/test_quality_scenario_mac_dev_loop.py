from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.web.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_software_assessment_dynamic_actions_stay_on_mature_routes():
    script = (
        ROOT / "quality_knowledge/web/static/software_assessment_qsv1.js"
    ).read_text(encoding="utf-8")

    assert "/quality-scenarios/workbench?scenario_id=" in script
    assert "/quality-scenarios/library" in script
    assert "/p0/quality-scenarios" not in script


def test_mac_dev_loop_is_test_only_and_keeps_source_and_qsv1_separate():
    launcher = (ROOT / "start_quality_scenario_mac_dev_loop.command").read_text(
        encoding="utf-8"
    )

    assert "MODE=TEST_ONLY_CONTROLLED_DATA" in launcher
    assert "DIRECT_QSV1_CANDIDATE_WRITE=NO" in launcher
    assert "DIRECT_QSV1_PUBLISH_WRITE=NO" in launcher
    assert "quality_scenario_source_fixture.db" in launcher
    assert "quality_scenario_v1_dev.db" in launcher
    assert 'QUALITY_SCENARIO_V1_DB_PATH="$QSV1_DB"' in launcher
    assert 'LEGACY_QUALITY_ISSUE_DB_PATH="$SOURCE_DB"' in launcher
    assert "main.py" in launcher
    assert "knowledge-web" in launcher
    assert "/software-assessment#quality-scenario-production" in launcher


def test_mac_dev_loop_legacy_entry_no_longer_targets_missing_script():
    wrapper = (
        ROOT / "start_w4_mac_functional_provider_golden.command"
    ).read_text(encoding="utf-8")

    assert "start_w4_mac_functional_golden.command" not in wrapper
    assert "start_quality_scenario_mac_dev_loop.command" in wrapper
    assert "/bin/zsh" in wrapper


def test_mature_software_assessment_still_exposes_controlled_product_entry(tmp_path):
    client = TestClient(create_app(tmp_path / "mature.db"))
    page = client.get("/software-assessment")
    assert page.status_code == 200
    assert "data-sa-qsv1" in page.text
    assert 'href="/p0/quality-scenarios/workbench"' not in page.text

# Browser gate sync marker
