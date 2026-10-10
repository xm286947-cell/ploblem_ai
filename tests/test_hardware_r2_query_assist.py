"""Deterministic S1 red-light tests; synthetic rows are not Formal Release."""
from __future__ import annotations
from services.hardware_case_ai_retrieval import HardwareCaseAIRetrievalService
from services.hardware_assisted_search import HardwareAssistedSearch

ROWS = [
    {"knowledge_id": "KO-A0152", "business_case_id": "A0152", "title": "CPU_串口输出弱上拉导致串口屏乱码", "evidence_refs": ["E-1"], "interface": "串口", "design_constraint": "确认带载 VIH"},
    {"knowledge_id": "KO-A0207", "business_case_id": "A0207", "title": "模拟量偏差（ADC参考源不准）问题分析报告", "evidence_refs": ["E-2"], "interface": "模拟量输出", "design_constraint": "参考源电路精度要求"},
]


class CaseStore:
    def search_cases(self, query="", *, role="CONSUMER", statuses=None, historical=False):
        return {"results": []}


class Consumption:
    def get(self, knowledge_id):
        return next((dict(x) for x in ROWS if x["knowledge_id"] == knowledge_id), None)

    def search(self, text="", *, limit=100, business_case_id=None, interface=None, signal=None, device=None):
        items = [x for x in ROWS if not business_case_id or x["business_case_id"] == business_case_id]
        if interface:
            items = [x for x in items if x["interface"] == interface]
        if signal or device:
            items = []
        if text:
            terms = text.lower().split()
            items = [x for x in items if all(any(term in str(value).lower() for value in x.values() if isinstance(value, str)) for term in terms)]
        return {"results": [dict(x, match_score=10, match_reasons=[{"matched_field": "title", "matched_text": text, "weight": 10}]) for x in items[:limit]]}


class Agent:
    def understand(self, query):
        assert "模拟量" in query
        return {"intent": "DESIGN_REUSE", "queries": ["模拟量"], "trace": {"task_id": "TEST_TASK", "run_id": "TEST_RUN", "provider_calls": 1}}


def test_two_view_shared_search_with_fake_agent_and_projection_ids():
    service = HardwareCaseAIRetrievalService(CaseStore(), consumption_service=Consumption(), query_agent=Agent())
    ui = HardwareAssistedSearch(service, Consumption())
    question = "设计模拟量电路时，有什么经验可以借鉴？"
    case = service.search_cases(question)
    knowledge = ui.search(question)
    assert [x["case_id"] for x in case["results"]] == ["A0207"]
    assert [x["business_case_id"] for x in knowledge["results"]] == ["A0207"]
    assert knowledge["retrieval"]["query_agent"]["trace"]["run_id"] == "TEST_RUN"
    assert knowledge["results"][0]["evidence_refs"] == ["E-2"]


def test_direct_ids_symptom_synonym_and_negative_query():
    svc = HardwareCaseAIRetrievalService(CaseStore(), consumption_service=Consumption())
    assert [x["case_id"] for x in svc.search_cases("A0207")["results"]] == ["A0207"]
    assert [x["case_id"] for x in svc.search_cases("串口乱码")["results"]] == ["A0152"]
    assert svc.search_cases("复位问题")["results"] == []
    ui = HardwareAssistedSearch(svc, Consumption())
    assert [x["business_case_id"] for x in ui.search("A0207")["results"]] == ["A0207"]
    assert [x["business_case_id"] for x in ui.search("", interface="串口")["results"]] == ["A0152"]
    assert ui.search("复位问题")["results"] == []
