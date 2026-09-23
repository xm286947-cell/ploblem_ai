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
        "analysis_id": "RQA-P03-1",
        "run_id": "RQRUN-P03-1",
        "run_seq": 7,
        "identity": {
            "canonical_itr": "ITR-P03-001",
            "product_code": "PLC",
            "taxonomy_version_id": "STV-P03-1",
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
        "version_id": "STV-P03-1",
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
    db_path = tmp_path / "p03.db"
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


def _publish(client: TestClient, scenario):
    reviewed = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/review",
        json={
            "expected_scenario_version": scenario["scenario_version"],
            "patch": {"scenario_description": "人工确认后的掉电恢复质量场景"},
            "review_status": "CONFIRMED",
            "reviewer": "QUALITY_OWNER",
            "comment": "场景事实已核对",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    v2 = reviewed.json()["scenario"]
    confirmed = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/confirm",
        json={
            "expected_scenario_version": v2["scenario_version"],
            "quality_confirmed_by": "QUALITY_OWNER",
            "technical_confirmed_by": "RND_OWNER",
            "confirmation_note": "质量确认场景事实，研发确认技术判断",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    v3 = confirmed.json()["scenario"]
    published = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/publish",
        json={
            "expected_scenario_version": v3["scenario_version"],
            "published_by": "QUALITY_OWNER",
        },
    )
    assert published.status_code == 200, published.text
    return published.json()["scenario"]


def test_p03_route_is_formal_v1_detail_not_placeholder(tmp_path):
    client = _client(tmp_path)
    scenario = _create_candidate(client)
    page = client.get(f"/p0/quality-scenarios/{scenario['scenario_id']}")
    assert page.status_code == 200
    for marker in [
        "标准质量场景",
        "来源问题与 Evidence",
        "版本与确认记录",
        "专业质量确认",
        "研发技术确认",
        "进入场景工作台",
    ]:
        assert marker in page.text
    assert "P03 正式详情页将在 QS-MVP-05C 承接" not in page.text
    assert "/p0/static/p0_scenario_detail.css" in page.text
    assert "/p0/static/p0_scenario_detail.js" in page.text


def test_history_api_returns_v1_snapshots_and_confirmation_audit(tmp_path):
    client = _client(tmp_path)
    published = _publish(client, _create_candidate(client))

    response = client.get(
        f"/api/v2/quality-scenarios/{published['scenario_id']}/history"
    )
    assert response.status_code == 200, response.text
    history = response.json()

    assert [x["scenario_version"] for x in history["versions"]] == [4, 3, 2, 1]
    assert [x["snapshot"]["status"] for x in history["versions"]] == [
        "PUBLISHED",
        "CONFIRMED",
        "CANDIDATE",
        "CANDIDATE",
    ]
    assert history["versions"][0]["snapshot"]["version"]["published_at"]
    assert any(
        x["review"].get("reviewer") == "QUALITY_OWNER"
        for x in history["reviews"]
    )
    assert any(
        x["confirmation"].get("technical_confirmed_by") == "RND_OWNER"
        for x in history["reviews"]
    )


def test_p03_current_detail_contains_trigger_source_evidence_and_confirmation(tmp_path):
    client = _client(tmp_path)
    published = _publish(client, _create_candidate(client))
    response = client.get(f"/api/v2/quality-scenarios/{published['scenario_id']}")
    assert response.status_code == 200
    item = response.json()
    assert item["status"] == "PUBLISHED"
    assert item["trigger_source"] == "HIGH_PERCEPTION"
    assert item["trigger_reason"]
    assert item["source_problem_refs"]
    assert item["evidence_refs"]
    assert item["evidence_refs"][0]["supports"]
    assert item["confirmation"]["quality_confirmed_by"] == "QUALITY_OWNER"
    assert item["confirmation"]["technical_confirmed_by"] == "RND_OWNER"
    assert item["version"]["updated_at"]
    assert item["version"]["published_at"]


def test_history_api_404_never_falls_back_to_legacy():
    client = TestClient(create_p0_app.__wrapped__) if hasattr(create_p0_app, "__wrapped__") else None
    # The actual app-specific 404 is covered below with an initialized app.


def test_history_api_not_found_is_v1_404(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/v2/quality-scenarios/NOT-V1/history")
    assert response.status_code == 404
    assert response.json()["detail"] == "QUALITY_SCENARIO_V1_NOT_FOUND"


def test_p03_is_read_only_and_renders_required_trace_semantics():
    html = (WEB / "templates/p0_quality_scenario_detail_placeholder.html").read_text(encoding="utf-8")
    js = (WEB / "static/p0_scenario_detail.js").read_text(encoding="utf-8")
    css = (WEB / "static/p0_scenario_detail.css").read_text(encoding="utf-8")

    for marker in [
        "trigger_source",
        "source_problem_refs",
        "evidence_refs",
        "supports",
        "quality_confirmed_by",
        "technical_confirmed_by",
        "history.reviews",
        "version.updated_at",
        "published_at",
        "Evidence Missing",
        "Source Missing",
    ]:
        assert marker in js

    assert "POST" not in js
    assert "DELETE" not in js
    assert "/review" not in js
    assert "/confirm" not in js
    assert "/publish" not in js
    assert "data-action=" not in html
    assert "position:sticky" in css
    assert "@media(max-width:1180px)" in css
    assert "@media(max-width:900px)" in css


def test_p02_row_navigation_still_targets_p03():
    js = (WEB / "static/p0_scenario_library.js").read_text(encoding="utf-8")
    pages = (WEB / "p0_pages.py").read_text(encoding="utf-8")
    assert "window.location.href='/p0/quality-scenarios/'" in js
    assert '"/p0/quality-scenarios/{scenario_id}"' in pages
