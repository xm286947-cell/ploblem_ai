from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.overall_navigation import (
    append_overall_return_state,
    normalize_overall_return_state,
)
from quality_knowledge.web.p0_pages import create_p0_insights_router


class _ScenarioService:
    def scenario_detail(self, scenario_id: str):
        if scenario_id != "QS-R2-W2-1":
            return None
        return {
            "scenario_id": scenario_id,
            "scenario_name": "现场网络恢复",
            "status": "PUBLISHED",
            "lifecycle": "运行执行",
            "business_activity": "现场恢复",
            "quality_focus": "连续性",
            "trigger_summary": "链路抖动",
            "failure_mode_summary": "通信中断",
            "evidence_refs": [
                {
                    "evidence_id": "EV-R2-W2-1",
                    "evidence_type": "TEXT",
                    "source_ref": "SRC-R2-W2-1",
                    "supports": ["trigger"],
                }
            ],
            "source_problem_refs": [
                {
                    "source_ref": "SRC-R2-W2-1",
                    "source_id": "ITR-R2-W2-1",
                    "relation_type": "DERIVED_FROM",
                }
            ],
        }

    def source_trace(self, source_ref: str):
        if source_ref != "SRC-R2-W2-1":
            return None
        return {
            "items": [
                {
                    "scenario_id": "QS-R2-W2-1",
                    "scenario_name": "现场网络恢复",
                    "source": {
                        "source_ref": source_ref,
                        "relation_type": "DERIVED_FROM",
                    },
                }
            ]
        }


def _client():
    app = FastAPI()
    app.include_router(
        create_p0_insights_router(
            scenario_detail_service=_ScenarioService(),
        )
    )
    return TestClient(app)


def test_overall_return_context_contract_is_bounded_and_fail_closed():
    raw = json.dumps(
        {
            "contract": "overall-return-context/v1",
            "scroll_y": 420,
            "focus_id": "issues-q",
            "selected_object": "QK-42",
            "tab": "PRODUCT",
            "fields": {"issues.q": "PLC", "issues.month": "9月"},
        },
        ensure_ascii=False,
    )
    state = normalize_overall_return_state(raw)
    assert state["scroll_y"] == 420
    assert state["selected_object"] == "QK-42"

    target = append_overall_return_state(
        "/p0/issues?business_type=PLC&page=3#repeat-risk",
        state,
    )
    parsed = urlsplit(target)
    assert parsed.path == "/p0/issues"
    assert parsed.fragment == "repeat-risk"
    assert parse_qs(parsed.query)["business_type"] == ["PLC"]
    assert "overall_return_state" in parse_qs(parsed.query)

    for invalid in (
        '{"contract":"overall-return-context/v1","unknown":1}',
        '{"contract":"wrong","scroll_y":0}',
        '{"contract":"overall-return-context/v1","scroll_y":-1}',
    ):
        response = None
        try:
            normalize_overall_return_state(invalid)
        except Exception as exc:
            response = str(exc)
        assert response and "OVERALL_RETURN_CONTEXT_INVALID" in response


def test_p0_issue_round_trip_keeps_filtered_url_and_shared_state():
    client = _client()
    state = json.dumps(
        {
            "contract": "overall-return-context/v1",
            "scroll_y": 730,
            "focus_id": "issues-q",
            "selected_object": "QK-42",
            "fields": {"issues.q": "PLC-42", "issues.analysis_status": "FAILED"},
        },
        ensure_ascii=False,
    )
    response = client.get(
        "/p0/issues/QK-42",
        params={
            "return_to": "/p0/issues?business_type=PLC&page=3&page_size=20&selected_id=QK-42",
            "overall_return_state": state,
        },
    )
    assert response.status_code == 200
    assert "overall_navigation.js?v=" in response.text
    assert "/p0/issues?business_type=PLC&amp;page=3&amp;page_size=20&amp;selected_id=QK-42&amp;overall_return_state=" in response.text

    external = client.get(
        "/p0/issues/QK-42",
        params={"return_to": "https://evil.invalid/away"},
    )
    assert external.status_code == 400


def test_p0_issue_list_exposes_restorable_fields_and_detail_return_contract():
    client = _client()
    page = client.get("/p0/issues")
    assert page.status_code == 200
    for marker in (
        'data-overall-state-field="issues.business_type"',
        'data-overall-state-field="issues.month"',
        'data-overall-state-field="issues.analysis_status"',
        'data-overall-state-field="issues.q"',
    ):
        assert marker in page.text

    js = client.get("/p0/static/p0_issues.js")
    assert js.status_code == 200
    assert "data-overall-selected-object" in js.text
    assert "data-overall-preserve-context" in js.text
    assert "return_to" in js.text
    assert "OverallNavigation.restoreNow" in js.text

    nav = client.get("/p0/static/overall_navigation.js")
    assert nav.status_code == 200
    assert "overall-return-context/v1" in nav.text
    assert nav.headers["x-content-sha256"]


def test_quality_scenario_detail_source_chain_preserves_shared_return_state():
    client = _client()
    p04_context = json.dumps(
        {
            "contract": "p04-query-context/v1",
            "view": "PRODUCT",
            "selected_object": None,
            "filters": {"lifecycle": "运行执行"},
            "matrix_mode": "LIFECYCLE_X_BUSINESS_ACTIVITY",
            "page": 2,
            "page_size": 20,
        },
        ensure_ascii=False,
    )
    overall_state = json.dumps(
        {
            "contract": "overall-return-context/v1",
            "scroll_y": 925,
            "tab": "PRODUCT",
            "fields": {},
        },
        ensure_ascii=False,
    )

    detail = client.get(
        "/p0/quality-scenarios/QS-R2-W2-1",
        params={
            "return_to": "/p0/quality-scenario-insights",
            "p04_context": p04_context,
            "overall_return_state": overall_state,
        },
    )
    assert detail.status_code == 200
    assert "overall_return_state=" in detail.text
    assert "p04_context=" in detail.text

    source = client.get(
        "/p0/quality-scenario-sources/SRC-R2-W2-1",
        params={
            "return_to": "/p0/quality-scenario-insights",
            "p04_context": p04_context,
            "overall_return_state": overall_state,
        },
    )
    assert source.status_code == 200
    assert "overall_return_state=" in source.text
    assert "p04_context=" in source.text


def test_p04_shell_marks_shared_tab_and_filter_state():
    client = _client()
    page = client.get("/p0/quality-scenario-insights")
    assert page.status_code == 200
    assert 'data-overall-tab="PRODUCT"' in page.text
    assert 'data-overall-state-field="p04.lifecycle"' in page.text
    assert 'data-overall-state-field="p04.matrix_mode"' in page.text
