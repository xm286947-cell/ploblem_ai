from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from builder.ai_client import AIResponse
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.services import KnowledgeIssueService
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


class DeterministicOpenAIMock:
    """OpenAI-compatible completion mock for the real four-stage analyzer."""

    def complete(self, messages):
        prompt = messages[0]["content"]
        if "发生原因分析器" in prompt:
            result = {
                "root_cause_summary": "变更引入逻辑错误",
                "failure_mechanism": "异常输入触发错误分支",
                "contributing_factors": ["变更影响分析不足"],
                "occurrence_category": "CHANGE",
                "confidence": 0.88,
                "evidence": [],
            }
        elif "流出原因分析器" in prompt:
            result = {
                "escape_cause_summary": "测试未覆盖变更路径",
                "verification_gap": "缺少对应场景用例",
                "process_gap": "变更影响分析未闭环",
                "escape_category": "TEST_GAP",
                "confidence": 0.82,
                "evidence": [],
            }
        elif "再发风险分析器" in prompt:
            result = {
                "recurrence_risk_level": "HIGH",
                "recurrence_risk_reason": "措施偏单点修复",
                "existing_control_coverage": "仅修复当前代码",
                "residual_risk": "同类路径仍可能遗漏",
                "is_common_issue": True,
                "potential_affected_products": "PLC",
                "horizontal_action_needed": True,
            }
        else:
            result = {
                "capability_gaps": [
                    {
                        "gap_dimension": "MANAGEMENT",
                        "gap_category": "CHANGE_MANAGEMENT",
                        "gap_description": "缺少变更影响闭环",
                        "recommended_control": "建立变更影响检查清单",
                        "confidence": 0.8,
                        "evidence_refs": [],
                    }
                ]
            }
        return AIResponse(json.dumps(result, ensure_ascii=False), "deterministic-openai-mock", {})


def test_overall_import_analyze_and_human_confirm_golden_path(tmp_path, monkeypatch):
    p0_db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    legacy_db = tmp_path / "legacy.db"
    from quality_knowledge.web.app import create_legacy_quality_issue_router

    _, legacy_state = create_legacy_quality_issue_router(legacy_db, initialize_schema=True)
    legacy_state.mapping_configuration_service.migrate_yaml(
        ROOT / "quality_knowledge/config/plc_fields.yaml", "PLC", dry_run=False
    )
    app = create_p0_app(
        p0_db,
        stage_runner=object(),
        project_root=ROOT,
        legacy_quality_issue_db_path=legacy_db,
    )

    original_run = KnowledgeIssueService.run_issue_analysis
    mock = DeterministicOpenAIMock()

    def run_with_openai_mock(self, knowledge_id, root, *args, **kwargs):
        kwargs["client"] = mock
        kwargs["agent_id"] = "DEFAULT"
        return original_run(self, knowledge_id, root, *args, **kwargs)

    monkeypatch.setattr(KnowledgeIssueService, "run_issue_analysis", run_with_openai_mock)
    client = TestClient(app)

    workbook_path = tmp_path / "synthetic_issue.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["ITR单号", "问题描述", "产品", "月份"])
    sheet.append(["ITR-VNEXT-E2E-001", "合成问题：变更后偶发功能异常", "PLC", "2026-09"])
    workbook.save(workbook_path)

    with workbook_path.open("rb") as stream:
        preview = client.post(
            "/import/preview",
            data={"business_type": "PLC"},
            files={"file": (workbook_path.name, stream)},
        )
    assert preview.status_code == 200
    intake_id = re.search(r'name="intake_session_id" value="([^"]+)"', preview.text)
    assert intake_id
    confirmed_import = client.post(
        "/import/confirm",
        data={"intake_session_id": intake_id.group(1)},
        follow_redirects=False,
    )
    assert confirmed_import.status_code == 303
    assert client.get(confirmed_import.headers["location"]).status_code == 200

    with sqlite3.connect(legacy_db) as connection:
        row = connection.execute(
            "SELECT knowledge_id FROM quality_issue WHERE business_issue_id=?",
            ("ITR-VNEXT-E2E-001",),
        ).fetchone()
    assert row
    knowledge_id = row[0]

    analyzed = client.post(f"/analysis/{knowledge_id}", follow_redirects=False)
    assert analyzed.status_code == 303
    assert analyzed.headers["location"] == f"/issues/{knowledge_id}"
    detail = client.get(analyzed.headers["location"])
    assert detail.status_code == 200
    assert "变更引入逻辑错误" in detail.text
    assert "CHANGE_MANAGEMENT" in detail.text

    human_confirmed = client.post(
        f"/issues/{knowledge_id}/quality-confirmations",
        data={
            "occurrence_mrc": "REQUIREMENT_BASELINE_MISSING",
            "escape_mrc": "RELEASE_BRANCH_NOT_MERGED",
            "reason": "合成验收：评审确认",
            "evidence": "合成变更单与回归记录",
            "confirmed_by": "vnext-e2e",
        },
        follow_redirects=False,
    )
    assert human_confirmed.status_code == 303
    confirmed_detail = client.get(f"/issues/{knowledge_id}")
    assert "REQUIREMENT_BASELINE_MISSING" in confirmed_detail.text
    assert "RELEASE_BRANCH_NOT_MERGED" in confirmed_detail.text
    assert "人工确认" in confirmed_detail.text

    with sqlite3.connect(legacy_db) as connection:
        stages = connection.execute(
            "SELECT COUNT(*) FROM analysis_run WHERE knowledge_id=? AND status='COMPLETED'",
            (knowledge_id,),
        ).fetchone()[0]
        confirmation = connection.execute(
            "SELECT reason,evidence,confirmed_by FROM qc_human_revision WHERE knowledge_id=? ORDER BY rowid DESC LIMIT 1",
            (knowledge_id,),
        ).fetchone()
    with sqlite3.connect(p0_db) as connection:
        p0_copy_count = connection.execute(
            "SELECT COUNT(*) FROM quality_issue WHERE business_issue_id=?",
            ("ITR-VNEXT-E2E-001",),
        ).fetchone()[0]
    assert stages == 4
    assert tuple(confirmation) == (
        "合成验收：评审确认",
        "合成变更单与回归记录",
        "vnext-e2e",
    )
    assert p0_copy_count == 0
