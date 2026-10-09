"""Hardware R2 red-gate: real Formal semantics, injected agents, no fake Provider PASS."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.hardware_case_ai_retrieval import HardwareCaseAIRetrievalService
from services.hardware_knowledge_consumption import HardwareKnowledgeConsumptionService
from services.hardware_r2_agent_runtime import HardwareR2Agent, validate_advice
from quality_knowledge.web.hardware_r2_agent_api import create_hardware_r2_agent_router

A0207 = {
    "knowledge_id": "KO-A0207", "business_case_id": "A0207",
    "title": "模拟量偏差（ADC参考源不准）问题分析报告",
    "symptom": "模拟电流输出偏差",
    "root_cause": "运放 offset 和参考源电路不准，LDO 输出偏差",
    "engineering_rule": "模拟量输出精度依赖参考源精度",
    "design_constraint": "必须检查运放 offset、参考电压和 LDO 离散性",
    "verification_method": "修正参考源后测量模拟量输出精度",
    "evidence_refs": ["HCR1-EV-0207"],
}
A0152 = {
    "knowledge_id": "KO-A0152", "business_case_id": "A0152",
    "title": "CPU 串口输出配置弱上拉导致串口屏乱码",
    "symptom": "MCU 发送字符时经常出现乱码",
    "root_cause": "驱动模式弱上拉",
    "engineering_rule": "串口 TX 应满足 VIH",
    "evidence_refs": ["HCR1-EV-0152"],
}

class Store:
    def list_all(self):
        return [dict(A0152), dict(A0207)]

    def get(self, knowledge_id):
        return next((dict(row) for row in self.list_all()
                     if row["knowledge_id"] == knowledge_id), None)

class EmptyLocalCaseStore:
    def search_cases(self, query="", **kwargs):
        return {"contract_version": "hardware-case/v1", "results": []}

class QueryAgent:
    def __init__(self, status="COMPLETED"):
        self.status = status
        self.calls = 0

    def invoke(self, payload):
        self.calls += 1
        if self.status != "COMPLETED":
            return {"status": self.status, "data": None, "trace": None, "reason": "NO_PROVIDER"}
        return {"status": "COMPLETED",
                "data": {"intent": "DESIGN_REUSE", "queries": ["模拟量", "ADC参考源"]},
                "trace": {"task_id": "real-task-required", "run_id": "real-run-required",
                          "provider_calls": 1}, "reason": None}

class ConsumeAgent:
    def __init__(self, evidence="HCR1-EV-0207"):
        self.evidence = evidence

    def invoke(self, payload):
        return {"status": "COMPLETED", "trace": {"task_id": "injected-test-only"},
                "data": {"summary": "来自正式知识的设计检查",
                         "checks": [{"advice": "检查参考源精度",
                                     "knowledge_id": "KO-A0207",
                                     "field": "design_constraint",
                                     "evidence_id": self.evidence}]}}

@pytest.fixture()
def context():
    formal = HardwareKnowledgeConsumptionService(Store())
    agent = QueryAgent()
    search = HardwareCaseAIRetrievalService(
        EmptyLocalCaseStore(), consumption_service=formal, query_agent=agent)
    app = FastAPI()
    app.include_router(create_hardware_r2_agent_router(search, formal, ConsumeAgent()))
    return TestClient(app), search, agent

@pytest.mark.parametrize("phrase,case_id", [
    ("模拟量", "A0207"), ("ADC参考源", "A0207"), ("模拟量偏差", "A0207"),
    ("串口乱码", "A0152"), ("MCU", "A0152"), ("A0207", "A0207"),
])
def test_formal_positive_case_recall(context, phrase, case_id):
    client, search, _ = context
    case = search.search_cases(phrase)
    assert case_id in [row["case_id"] for row in case["results"]]
    page = client.get("/api/v2/hardware-r2/search", params={"text": phrase})
    assert page.status_code == 200
    assert case_id in [row["business_case_id"] for row in page.json()["results"]]

def test_long_natural_query_online_agent_injected_and_traced(context):
    client, _, agent = context
    response = client.get("/api/v2/hardware-r2/search",
                          params={"text": "设计模拟量电路时，有什么经验可以借鉴？"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["business_case_id"] == "A0207"
    assert payload["agent"]["status"] == "COMPLETED"
    assert payload["agent"]["trace"]["task_id"] == "real-task-required"
    assert agent.calls == 1

def test_negative_recall_is_not_broadened(context):
    client, _, agent = context
    payload = client.get("/api/v2/hardware-r2/search",
                         params={"text": "复位问题"}).json()
    assert payload["results"] == []
    assert agent.calls == 0

def test_public_formal_consumer_contract_stays_deterministic(context):
    _, search, agent = context
    formal = search.consumption_service
    assert formal.search("设计模拟量电路时，有什么经验可以借鉴？")["results"] == []
    assert agent.calls == 0

def test_fail_closed_on_fake_source_and_disabled_provider():
    row = A0207
    payload = {"summary": "a", "checks": [
        {"advice": "建议", "knowledge_id": "KO-A0207",
         "field": "design_constraint", "evidence_id": "UNKNOWN"}]}
    with pytest.raises(ValueError, match="ADVICE_SOURCE_REF_INVALID"):
        validate_advice(payload, row)
    agent = HardwareR2Agent("query", environ={})
    assert agent.invoke({"question": "hello"})["status"] == "DISABLED"
    assert agent.runtime is None

def test_consumer_analysis_is_on_demand_and_citation_checked(context):
    client, _, _ = context
    ok = client.post("/api/v2/hardware-r2/analyze", json={
        "knowledge_id": "KO-A0207", "task": "DESIGN_REUSE"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "COMPLETED"
    assert ok.json()["analysis"]["checks"][0]["evidence_id"] == "HCR1-EV-0207"
    bad_client = TestClient(FastAPI())
    from services.hardware_knowledge_consumption import HardwareKnowledgeConsumptionService
    formal = HardwareKnowledgeConsumptionService(Store())
    bad_client.app.include_router(create_hardware_r2_agent_router(
        HardwareCaseAIRetrievalService(EmptyLocalCaseStore(), consumption_service=formal),
        formal, ConsumeAgent(evidence="INVENTED")))
    fail = bad_client.post("/api/v2/hardware-r2/analyze", json={
        "knowledge_id": "KO-A0207", "task": "DESIGN_REUSE"})
    assert fail.json()["status"] == "EVIDENCE_REJECTED"
