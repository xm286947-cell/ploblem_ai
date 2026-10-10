from __future__ import annotations
import pytest
from services.hardware_engineering_consumption import (
    EngineeringAnalysisError, HardwareEngineeringConsumption,
)


class Formal:
    def get(self, knowledge_id):
        if knowledge_id != "KO-A0207":
            return None
        return {
            "knowledge_id": "KO-A0207", "business_case_id": "A0207",
            "design_constraint": "参考源电路必须满足精度要求；选用低offset高精度运放。",
            "verification_method": "检查REF与LDO相关电压偏差并验证输出精度。",
            "evidence_refs": ["E-2"],
        }


class GoodAgent:
    def analyze(self, payload):
        assert payload["task_intent"] == "DESIGN_REUSE"
        assert payload["read_only"] is True
        return {"task_intent": "DESIGN_REUSE", "items": [
            {"suggestion": "重点检查参考源电路精度", "source_field": "design_constraint",
             "source_quote": "参考源电路必须满足精度要求"}
        ], "_trace": {"run_id": "FAKE_RUN"}}


class BadAgent:
    def analyze(self, payload):
        return {"task_intent": payload["task_intent"], "items": [
            {"suggestion": "测试不支持的外部规格", "source_field": "design_constraint",
             "source_quote": "不存在的规格参数"}]}


def test_analysis_is_read_only_and_cited_to_exact_field():
    output = HardwareEngineeringConsumption(Formal(), agent=GoodAgent()).analyze("KO-A0207", "DESIGN_REUSE")
    assert output["items"][0]["business_case_id"] == "A0207"
    assert output["items"][0]["source_field"] == "design_constraint"
    assert output["case_evidence_refs"] == ["E-2"]
    assert output["evidence_binding"] == "CASE_LEVEL_ONLY"
    assert output["status"] == "AI_ADVISORY_REQUIRES_ENGINEERING_REVIEW"
    # The model's own prose must not be treated as evidence-backed engineering fact.
    assert output["items"][0]["suggestion"] == "参考源电路必须满足精度要求"
    assert output["items"][0]["advice_scope"] == "VERBATIM_FORMAL_EXCERPT"


def test_analysis_rejects_ungrounded_quote_and_unknown_case():
    with pytest.raises(EngineeringAnalysisError, match="ANALYSIS_UNGROUNDED_QUOTE"):
        HardwareEngineeringConsumption(Formal(), agent=BadAgent()).analyze("KO-A0207", "DESIGN_REUSE")
    with pytest.raises(EngineeringAnalysisError, match="FORMAL_KNOWLEDGE_NOT_FOUND"):
        HardwareEngineeringConsumption(Formal(), agent=GoodAgent()).analyze("UNKNOWN", "DESIGN_REUSE")
    with pytest.raises(EngineeringAnalysisError, match="TASK_INTENT_UNSUPPORTED"):
        HardwareEngineeringConsumption(Formal(), agent=GoodAgent()).analyze("KO-A0207", "SOMETHING_ELSE")


def test_internal_analysis_endpoint_is_closed_without_trusted_token():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from quality_knowledge.web.hardware_engineering_consumption_api import create_hardware_engineering_consumption_router
    service = HardwareEngineeringConsumption(Formal(), agent=GoodAgent())
    app = FastAPI()
    app.include_router(create_hardware_engineering_consumption_router(service))
    response = TestClient(app).post("/api/hardware-query/v1/analyze", json={"knowledge_id": "KO-A0207", "task_intent": "DESIGN_REUSE"})
    assert response.status_code == 503
    enabled_app = FastAPI()
    enabled_app.include_router(create_hardware_engineering_consumption_router(service, token="unit-test-token"))
    client = TestClient(enabled_app)
    assert client.post("/api/hardware-query/v1/analyze", json={"knowledge_id": "KO-A0207", "task_intent": "DESIGN_REUSE"}).status_code == 403
    approved = client.post("/api/hardware-query/v1/analyze", json={"knowledge_id": "KO-A0207", "task_intent": "DESIGN_REUSE"}, headers={"X-Hardware-Analysis-Token": "unit-test-token"})
    assert approved.status_code == 200
    assert approved.json()["evidence_binding"] == "CASE_LEVEL_ONLY"
