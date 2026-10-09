"""S1 Query Agent advisory guidance, trace, fallback and shared UI API."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.hardware_case_api import create_hardware_case_router
from services.hardware_case_ai_retrieval import HardwareCaseAIRetrievalService


class EmptyCases:
    def search_cases(self, query="", **kwargs):
        return {"contract_version": "hardware-case/v1", "results": []}


class Formal:
    row = {
        "knowledge_id": "KO-0207", "business_case_id": "A0207",
        "title": "模拟量偏差（ADC参考源不准）问题分析报告",
        "engineering_rule": "模拟量输出精度依赖REF基准源精度",
        "evidence_refs": ["EV-0207"],
    }

    def get(self, knowledge_id):
        return self.row if knowledge_id == "KO-0207" else None

    def search(self, text="", *, business_case_id=None, knowledge_id=None, **kwargs):
        if business_case_id and business_case_id != "A0207":
            return {"results": []}
        if knowledge_id and knowledge_id != "KO-0207":
            return {"results": []}
        if business_case_id or knowledge_id or not text or ("模拟量" in text and len(text) <= 6):
            return {"results": [{**self.row, "match_score": 1, "match_reasons": [
                {"matched_field": "title", "matched_text": "模拟量"}]}]}
        return {"results": []}


def valid_agent(query):
    assert "借鉴" in query
    return {"intent": "DESIGN_REUSE", "search_terms": ["模拟量"],
            "trace": {"task_id": "task-from-fake", "run_id": "run-from-fake", "provider_calls": 1}}


def test_agent_advisory_guidance_uses_formal_knowledge_not_synthetic_facts():
    svc = HardwareCaseAIRetrievalService(EmptyCases(), consumption_service=Formal(),
                                        query_agent=valid_agent)
    result = svc.search_cases("设计模拟量电路时，有什么经验可以借鉴？")
    assert [r["business_case_id"] for r in result["results"]] == ["A0207"]
    assert result["retrieval"]["query_understanding"]["online_agent"]["status"] == "COMPLETED"
    assert result["results"][0]["retrieval"]["why_hit"]["query_expansion"]["policy"] == "AGENT_QUERY"


def test_agent_disabled_or_invalid_trace_never_reports_online_success():
    question = "设计模拟量电路时，有什么经验可以借鉴？"
    absent = HardwareCaseAIRetrievalService(EmptyCases(), consumption_service=Formal())
    assert absent.search_cases(question)["retrieval"]["query_understanding"]["online_agent"]["status"] == "BLOCKED"
    agent_without_trace = lambda _: {"intent": "DESIGN_REUSE", "search_terms": ["模拟量"],
                                    "trace": {"task_id": "not-real", "run_id": "none", "provider_calls": 0}}
    invalid = HardwareCaseAIRetrievalService(EmptyCases(), consumption_service=Formal(),
                                            query_agent=agent_without_trace)
    result = invalid.search_cases(question)
    assert result["results"] == []
    assert result["retrieval"]["query_understanding"]["online_agent"]["status"] == "FAILED"


def test_shared_r2_api_rehydrates_published_formal_and_exposes_trace_state():
    svc = HardwareCaseAIRetrievalService(EmptyCases(), consumption_service=Formal(),
                                        query_agent=valid_agent)
    app = FastAPI()
    app.include_router(create_hardware_case_router(EmptyCases(), ai_search_service=svc))
    with TestClient(app) as client:
        response = client.get("/api/v2/hardware-cases/r2/knowledge-query",
                              params={"text": "设计模拟量电路时，有什么经验可以借鉴？"})
        assert response.status_code == 200
        payload = response.json()
        assert [r["business_case_id"] for r in payload["results"]] == ["A0207"]
        assert payload["results"][0]["engineering_rule"] == Formal.row["engineering_rule"]
        assert payload["retrieval"]["query_understanding"]["online_agent"]["status"] == "COMPLETED"
