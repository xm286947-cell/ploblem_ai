import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.app import create_app


class _FakeResponse:
    model = "test-model"

    def __init__(self, content):
        self.content = json.dumps(content, ensure_ascii=False)


class _BrowserHandoffAI:
    def complete(self, messages):
        return _FakeResponse(
            {
                "fields": {
                    "customer_experience": {"value": "异常掉电后关键计数丢失", "evidence_ids": ["cs.description"], "confidence": 0.9},
                    "expected_quality_state": {"value": "重新上电后关键计数正确恢复", "evidence_ids": ["cs.description"], "confidence": 0.8},
                    "preconditions": {"value": "PLC 正常运行", "evidence_ids": ["cs.description"], "confidence": 0.9},
                    "root_cause": {"value": "保持变量写入未完成", "evidence_ids": ["cs.root_cause"], "confidence": 1.0},
                    "recovery_method": {"value": "重新上电恢复运行", "evidence_ids": ["structured.recovery_measure"], "confidence": 0.95},
                    "related_objects": {"value": "PLC AM600", "evidence_ids": ["structured.product_model"], "confidence": 0.9},
                    "quality_requirement_candidate": {"value": "异常掉电后关键运行数据能够正确恢复", "evidence_ids": ["cs.description", "cs.root_cause"], "confidence": 0.75},
                    "lifecycle_stage": {"value": "运行执行", "evidence_ids": ["cs.description", "cs.phase"], "confidence": 0.85},
                    "business_activity_scene": {"value": "掉电数据保持与上电恢复", "evidence_ids": ["cs.description"], "confidence": 0.9},
                },
                "lifecycle_code": "RUNTIME_EXECUTION",
                "activity_code": "POWER_LOSS_RETENTION_RECOVERY",
                "match_reason": "有掉电和重新上电的数据恢复证据",
                "missing_condition": "系统规模未知",
                "questions": [
                    {
                        "field_name": "scale_or_load",
                        "reason": "原始问题未给出系统规模",
                        "question": "现场参与设备规模是多少？",
                        "evidence_needed": ["现场拓扑或设备数量"],
                    }
                ],
            }
        )


def _setup_browser_case(tmp_path: Path):
    source_db = tmp_path / "source.db"
    scenario_db = tmp_path / "scenario.db"
    initializer = P0Initializer(
        manifest_path=Path("quality_knowledge/config/p0_seed_manifest.json"),
        plc_seed_path=Path("quality_knowledge/config/plc_fields.yaml"),
    )
    initializer.initialize(scenario_db)
    app = create_app(source_db, scenario_db=scenario_db)
    material_id, _ = app.state.material_repository.add_material(
        app.state.material_repository.group("ITR-CS"),
        "ITR20260918001CS",
        {
            "问题信息_问题描述": "PLC 正常运行时异常掉电，重新上电后关键计数丢失",
            "问题信息_问题原因定位": "保持变量写入未完成",
            "问题信息_问题发生阶段": "终端正常使用",
            "问题信息_产品型号": "PLC AM600",
            "问题信息_问题领域": "软件",
            "问题信息_客户行业": "锂电",
            "问题信息_客户分级": "A",
            "问题信息_问题发生地点": "客户现场",
            "问题信息_问题发生地区归属": "华东",
            "问题信息_已用时长": "6个月",
            "问题信息_故障台数": "12",
            "问题信息_不良问题频率": "3次/周",
            "问题处理结果_问题解决方案": "重新上电恢复运行",
            "技术根因分析与纠正_软件模块": "RetainManager",
            "技术根因分析与纠正_软件功能": "掉电保持",
        },
        "synthetic.xlsx",
        "Sheet1",
        2,
    )
    app.state.reverse_quality_service.ai_client = _BrowserHandoffAI()
    return app, material_id


@pytest.mark.parametrize(
    ("trigger_source", "trigger_reason"),
    [
        ("HIGH_PERCEPTION", "客户现场高感知停线问题，纳入正式质量场景库"),
        ("RND_VALUE", "研发认定该恢复场景具有复用价值"),
    ],
)
def test_browser_handoff_uses_existing_p01_and_preserves_contract(tmp_path, trigger_source, trigger_reason):
    app, material_id = _setup_browser_case(tmp_path)
    client = TestClient(app)

    source_page = client.get(f"/reverse-quality/{material_id}")
    assert source_page.status_code == 200

    analysed = client.post(
        f"/reverse-quality/{material_id}/analyse",
        data={"product_code": "PLC"},
        follow_redirects=False,
    )
    assert analysed.status_code == 303
    result_page = client.get(f"/reverse-quality/{material_id}")
    assert result_page.status_code == 200
    assert f'action="/reverse-quality/{material_id}/handoff"' in result_page.text
    assert "HIGH_PERCEPTION" in result_page.text
    assert "RND_VALUE" in result_page.text
    assert "CANDIDATE" in result_page.text
    assert "不自动确认" in result_page.text
    assert "不自动发布" in result_page.text
    assert "/api/v2/quality-scenarios/candidates/from-reverse" not in result_page.text
    assert "现场参与设备规模是多少？" in result_page.text
    assert "Source：ITR:ITR20260918001" in result_page.text
    assert "cs.description" in result_page.text

    handoff = client.post(
        f"/reverse-quality/{material_id}/handoff",
        data={
            "trigger_source": trigger_source,
            "trigger_reason": trigger_reason,
            "reviewer": "质量场景研发负责人",
        },
        follow_redirects=False,
    )
    assert handoff.status_code == 303
    assert handoff.headers["location"] == "/p0/quality-scenarios/workbench"

    candidates = client.get("/api/v2/quality-scenarios/candidates")
    assert candidates.status_code == 200
    payload = candidates.json()
    assert payload["total"] == 1
    candidate = payload["items"][0]
    assert candidate["status"] == "CANDIDATE"
    assert candidate["trigger_source"] == trigger_source
    assert candidate["trigger_reason"] == trigger_reason
    assert candidate["missing_information"]
    assert candidate["missing_information"][0]["question"] == "现场参与设备规模是多少？"
    assert "QUALITY_CONCERN_REQUIRED" in candidate["blockers"]
    assert any(ref.get("canonical_itr") == "ITR20260918001" for ref in candidate["source_problem_refs"])
    assert any(ref.get("evidence_id") == "cs.description" for ref in candidate["evidence_refs"])
    assert candidate["review"]["review_status"] == "PENDING"
    assert not candidate["review"].get("quality_confirmed_by")
    assert not candidate["review"].get("technical_confirmed_by")

    workbench = client.get("/p0/quality-scenarios/workbench")
    assert workbench.status_code == 200
    assert "/p0/static/p0_scenario_workbench.js" in workbench.text
    candidate_detail = client.get(f"/api/v2/quality-scenarios/candidates/{candidate['scenario_id']}")
    assert candidate_detail.status_code == 200
    assert candidate_detail.json()["status"] == "CANDIDATE"


def test_browser_handoff_boundary_has_no_test_api_bypass_or_auto_transition():
    app_source = Path("quality_knowledge/web/app.py").read_text(encoding="utf-8")
    template = Path("quality_knowledge/web/templates/reverse_quality_issue.html").read_text(encoding="utf-8")
    handoff_route = app_source.split("@app.post('/reverse-quality/{material_id}/handoff'", 1)[1].split("    def filters", 1)[0]
    assert "create_from_reverse" in handoff_route
    assert ".confirm" not in handoff_route.lower()
    assert ".publish" not in handoff_route.lower()
    assert "/api/v2/quality-scenarios/candidates/from-reverse" not in template
    assert "scenario_library['candidates']" in handoff_route


def test_scenario_db_is_initialized_by_product_entrypoint(tmp_path):
    scenario_db = tmp_path / "scenario.db"
    initializer = P0Initializer(
        manifest_path=Path("quality_knowledge/config/p0_seed_manifest.json"),
        plc_seed_path=Path("quality_knowledge/config/plc_fields.yaml"),
    )
    result = initializer.initialize(scenario_db)
    assert result["initialization_state"] == "READY"
