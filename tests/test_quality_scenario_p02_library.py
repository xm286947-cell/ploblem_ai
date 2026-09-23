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


def _reverse_result(*, analysis_id="RQA-P02-1", run_id="RQRUN-P02-1", itr="ITR-P02-001"):
    return {
        "result_version": "reverse-quality-v0.1",
        "analysis_id": analysis_id,
        "run_id": run_id,
        "run_seq": 6,
        "identity": {
            "canonical_itr": itr,
            "product_code": "PLC",
            "taxonomy_version_id": "STV-P02-1",
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
        "version_id": "STV-P02-1",
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
    db_path = tmp_path / "p02.db"
    _initializer().initialize(db_path)
    return TestClient(create_p0_app(db_path, stage_runner=None))


def _create_candidate(client: TestClient, *, analysis_id, run_id, itr, source, reason):
    response = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": _reverse_result(
                analysis_id=analysis_id,
                run_id=run_id,
                itr=itr,
            ),
            "taxonomy": _taxonomy(),
            "trigger_source": source,
            "trigger_reason": reason,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["scenario"]


def _publish(client: TestClient, scenario):
    reviewed = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/review",
        json={
            "expected_scenario_version": scenario["scenario_version"],
            "patch": {},
            "review_status": "CONFIRMED",
            "reviewer": "QUALITY_OWNER",
            "comment": "P02 Golden review",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    item = reviewed.json()["scenario"]
    confirmed = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/confirm",
        json={
            "expected_scenario_version": item["scenario_version"],
            "quality_confirmed_by": "QUALITY_OWNER",
            "technical_confirmed_by": "RND_OWNER",
            "confirmation_note": "P02 Golden confirm",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    item = confirmed.json()["scenario"]
    published = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/publish",
        json={
            "expected_scenario_version": item["scenario_version"],
            "published_by": "QUALITY_OWNER",
        },
    )
    assert published.status_code == 200, published.text
    return published.json()["scenario"]


def test_p02_route_uses_existing_shell_and_defaults_to_published(tmp_path):
    client = _client(tmp_path)
    page = client.get("/p0/quality-scenarios")
    assert page.status_code == 200
    for marker in [
        "质量场景库",
        "默认只展示已发布场景",
        'value="PUBLISHED" selected',
        "来源问题",
        "最近更新时间",
    ]:
        assert marker in page.text
    assert 'class="side-nav"' in page.text
    assert "/p0/static/p0_scenario_library.css" in page.text
    assert "/p0/static/p0_scenario_library.js" in page.text
    assert client.get("/p0/static/p0_scenario_library.css").status_code == 200
    assert client.get("/p0/static/p0_scenario_library.js").status_code == 200


def test_default_published_query_excludes_candidate_and_filters_all_frozen_dimensions(tmp_path):
    client = _client(tmp_path)
    published = _publish(
        client,
        _create_candidate(
            client,
            analysis_id="RQA-P02-PUB",
            run_id="RQRUN-P02-PUB",
            itr="ITR-P02-PUB",
            source="HIGH_PERCEPTION",
            reason="客户高感知问题",
        ),
    )
    candidate = _create_candidate(
        client,
        analysis_id="RQA-P02-CAND",
        run_id="RQRUN-P02-CAND",
        itr="ITR-P02-CAND",
        source="RND_VALUE",
        reason="研发复用价值",
    )

    default_scope = client.get("/api/v2/quality-scenarios", params={"status": "PUBLISHED"})
    assert default_scope.status_code == 200
    ids = [item["scenario_id"] for item in default_scope.json()["items"]]
    assert published["scenario_id"] in ids
    assert candidate["scenario_id"] not in ids

    filtered = client.get(
        "/api/v2/quality-scenarios",
        params={
            "status": "PUBLISHED",
            "product_code": "PLC",
            "lifecycle_stage_code": "RUNTIME_EXECUTION",
            "business_activity_code": "POWER_LOSS_RETENTION_RECOVERY",
            # V1 permits a quality concern name without a code; P02 must still filter it.
            "quality_concern_code": "数据完整性",
            "q": "数据完整性",
        },
    )
    assert filtered.status_code == 200, filtered.text
    body = filtered.json()
    assert body["total"] == 1
    assert body["items"][0]["scenario_id"] == published["scenario_id"]
    assert body["items"][0]["status"] == "PUBLISHED"
    assert body["items"][0]["source_problem_refs"]
    assert body["items"][0]["version"]["updated_at"]


def test_p02_keyword_search_covers_business_and_quality_fields(tmp_path):
    client = _client(tmp_path)
    published = _publish(
        client,
        _create_candidate(
            client,
            analysis_id="RQA-P02-SEARCH",
            run_id="RQRUN-P02-SEARCH",
            itr="ITR-P02-SEARCH",
            source="HIGH_PERCEPTION",
            reason="客户高感知问题",
        ),
    )
    for q in ["数据完整性", "掉电数据保持与上电恢复", "异常掉电", "PLC"]:
        response = client.get(
            "/api/v2/quality-scenarios",
            params={"status": "PUBLISHED", "q": q},
        )
        assert response.status_code == 200
        assert published["scenario_id"] in [x["scenario_id"] for x in response.json()["items"]]


def test_p02_is_read_only_and_rows_target_the_reserved_p03_route():
    html = (WEB / "templates/p0_quality_scenario_library.html").read_text(encoding="utf-8")
    js = (WEB / "static/p0_scenario_library.js").read_text(encoding="utf-8")
    assert "data-detail" in js
    assert "window.location.href='/p0/quality-scenarios/'" in js
    assert "version.updated_at" in js
    assert "source_problem_refs" in js
    assert "DELETE" not in js
    assert "POST" not in js
    assert "/publish" not in js
    assert "/review" not in js
    assert "/confirm" not in js
    assert "删除" not in html


def test_p03_reserved_route_never_falls_back_to_legacy_scenario_detail(tmp_path):
    client = _client(tmp_path)
    page = client.get("/p0/quality-scenarios/QSV1C-DEMO")
    assert page.status_code == 200
    assert "P03 正式详情页将在 QS-MVP-05C 承接" in page.text
    assert "QSV1C-DEMO" in page.text
    assert "不跳转旧 Scenario 对象" in page.text


def test_p02_has_loading_empty_no_result_error_and_1280_safe_layout():
    html = (WEB / "templates/p0_quality_scenario_library.html").read_text(encoding="utf-8")
    css = (WEB / "static/p0_scenario_library.css").read_text(encoding="utf-8")
    js = (WEB / "static/p0_scenario_library.js").read_text(encoding="utf-8")
    for marker in ["qsl-loading", "data-empty-state", "data-no-result-state", "data-error-state"]:
        assert marker in html
    assert "min-width:1120px" in css
    assert "@media(max-width:1320px)" in css
    assert "status=PUBLISHED" not in html  # default is a form value, not a hard-coded hidden query
    assert "hasNarrow" in js


def test_navigation_distinguishes_workbench_library_and_detail():
    base = (WEB / "templates/p0_base.html").read_text(encoding="utf-8")
    pages = (WEB / "p0_pages.py").read_text(encoding="utf-8")
    assert 'href="/p0/quality-scenarios/workbench">场景工作台</a>' in base
    assert 'href="/p0/quality-scenarios">质量场景库</a>' in base
    assert "request.url.path == '/p0/quality-scenarios/workbench'" in base
    assert '"/p0/quality-scenarios/{scenario_id}"' in pages
