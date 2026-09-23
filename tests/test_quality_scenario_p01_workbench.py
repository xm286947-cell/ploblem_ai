from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "quality_knowledge" / "web"


def _initializer() -> P0Initializer:
    return P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )


def _reverse_result():
    return {
        "result_version": "reverse-quality-v0.1",
        "analysis_id": "RQA-P01-1",
        "run_id": "RQRUN-P01-1",
        "run_seq": 5,
        "identity": {
            "canonical_itr": "ITR-P01-001",
            "product_code": "PLC",
            "taxonomy_version_id": "STV-P01-1",
        },
        "fields": {
            "lifecycle_stage": {
                "value": "运行执行",
                "source_type": "FACT",
                "evidence_ids": ["cs.phase"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "business_activity_scene": {
                "value": "掉电数据保持与上电恢复",
                "source_type": "FACT",
                "evidence_ids": ["cs.description"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "customer_experience": {
                "value": "掉电后关键计数丢失",
                "source_type": "FACT",
                "evidence_ids": ["cs.description"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "quality_risk": {
                "value": "数据完整性",
                "source_type": "INFERRED",
                "evidence_ids": ["cs.description"],
                "confidence": 0.7,
                "review_status": "PENDING",
            },
            "expected_quality_state": {
                "value": "重新上电后计数正确恢复",
                "source_type": "INFERRED",
                "evidence_ids": ["cs.description"],
                "confidence": 0.8,
                "review_status": "PENDING",
            },
            "trigger_condition": {
                "value": "运行中异常掉电",
                "source_type": "FACT",
                "evidence_ids": ["cs.description"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
        },
        "missing_information": [],
    }


def _taxonomy():
    return {
        "version_id": "STV-P01-1",
        "lifecycles": [
            {"lifecycle_code": "RUNTIME_EXECUTION", "label_zh": "运行执行", "enabled": 1}
        ],
        "activities": [
            {
                "activity_code": "POWER_LOSS_RETENTION_RECOVERY",
                "lifecycle_code": "RUNTIME_EXECUTION",
                "label_zh": "掉电数据保持与上电恢复",
                "objective": "保证掉电后关键数据正确恢复",
                "chain_text": "运行 → 掉电 → 上电 → 恢复",
                "enabled": 1,
            }
        ],
    }


def _client(tmp_path) -> TestClient:
    db_path = tmp_path / "p01.db"
    _initializer().initialize(db_path)
    return TestClient(create_p0_app(db_path, stage_runner=None))


def _create_candidate(client: TestClient):
    response = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": _reverse_result(),
            "taxonomy": _taxonomy(),
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "客户高感知问题进入场景深挖",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["scenario"]


def test_p01_workbench_route_assets_and_existing_shell(tmp_path):
    client = _client(tmp_path)
    page = client.get("/p0/quality-scenarios/workbench")
    assert page.status_code == 200
    for marker in [
        "场景工作台",
        "场景队列",
        "来源与 Evidence",
        "专业质量确认人",
        "研发技术确认人",
        "保存修订",
        "发布",
    ]:
        assert marker in page.text
    assert 'class="side-nav"' in page.text
    assert "/p0/static/p0_scenario_workbench.css" in page.text
    assert "/p0/static/p0_scenario_workbench.js" in page.text
    assert client.get("/p0/static/p0_scenario_workbench.css").status_code == 200
    assert client.get("/p0/static/p0_scenario_workbench.js").status_code == 200


def test_p01_uses_one_golden_path_and_no_approval_workflow():
    html = (WEB / "templates/p0_quality_scenario_workbench.html").read_text(encoding="utf-8")
    js = (WEB / "static/p0_scenario_workbench.js").read_text(encoding="utf-8")
    for endpoint in [
        "/quality-scenarios?",
        "/quality-scenarios/",
        "/review",
        "/confirm",
        "/reject",
        "/publish",
    ]:
        assert endpoint in js
    assert "HIGH_PERCEPTION" in js and "RND_VALUE" in js
    assert "WAIT_APPROVAL" not in html + js
    assert "WAIT_RND_APPROVAL" not in html + js
    assert "进入高感知流程" not in html + js
    assert "进入研发流程" not in html + js


def test_p01_layout_is_dense_three_column_desktop_with_trace_fallback():
    css = (WEB / "static/p0_scenario_workbench.css").read_text(encoding="utf-8")
    assert ".qs-workbench-grid{display:grid;grid-template-columns:" in css
    assert "minmax(220px" in css and "minmax(430px" in css and "minmax(255px" in css
    assert "@media(max-width:1240px)" in css
    assert ".qs-trace-panel{grid-column:1/-1" in css
    assert "@media(max-width:900px)" in css


def test_workbench_list_api_supports_status_and_filters(tmp_path):
    client = _client(tmp_path)
    created = _create_candidate(client)

    listing = client.get(
        "/api/v2/quality-scenarios",
        params={
            "status": "CANDIDATE",
            "product_code": "PLC",
            "lifecycle_stage_code": "RUNTIME_EXECUTION",
            "q": "掉电",
        },
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["scenario_id"] == created["scenario_id"]
    assert body["items"][0]["trigger_source"] == "HIGH_PERCEPTION"

    empty = client.get("/api/v2/quality-scenarios", params={"status": "PUBLISHED"})
    assert empty.status_code == 200
    assert empty.json()["total"] == 0

    invalid = client.get("/api/v2/quality-scenarios", params={"status": "WAIT_APPROVAL"})
    assert invalid.status_code == 400


def test_reject_requires_complete_human_audit(tmp_path):
    client = _client(tmp_path)
    created = _create_candidate(client)
    scenario_id = created["scenario_id"]

    missing_comment = client.post(
        f"/api/v2/quality-scenarios/{scenario_id}/reject",
        json={
            "expected_scenario_version": created["scenario_version"],
            "reviewer": "QUALITY_OWNER",
            "comment": "",
        },
    )
    assert missing_comment.status_code == 400
    assert missing_comment.json()["detail"] == "SCENARIO_REJECT_REVIEWER_COMMENT_REQUIRED"

    rejected = client.post(
        f"/api/v2/quality-scenarios/{scenario_id}/reject",
        json={
            "expected_scenario_version": created["scenario_version"],
            "reviewer": "QUALITY_OWNER",
            "comment": "不具备场景沉淀价值",
        },
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()["scenario"]
    assert body["status"] == "REJECTED"
    assert body["review"]["reviewer"] == "QUALITY_OWNER"
    assert body["review"]["comment"] == "不具备场景沉淀价值"


def test_navigation_exposes_p01_inside_existing_p0_shell():
    base = (WEB / "templates/p0_base.html").read_text(encoding="utf-8")
    pages = (WEB / "p0_pages.py").read_text(encoding="utf-8")
    assert 'href="/p0/quality-scenarios/workbench"' in base
    assert '"/p0/quality-scenarios/workbench"' in pages
    assert "create_p0_insights_router" in pages
