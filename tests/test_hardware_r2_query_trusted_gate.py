"""P0: public search must never trigger paid Provider; only trusted NON_PROD POST may."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.hardware_assisted_query_api import create_hardware_assisted_query_router
from services.hardware_assisted_search import HardwareAssistedSearch
from services.hardware_case_ai_retrieval import HardwareCaseAIRetrievalService


class Cases:
    def search_cases(self, query="", *, role="CONSUMER", statuses=None, historical=False):
        return {"results": []}


class Formal:
    class Store:
        def list_all(self):
            return [{"title": "模拟量偏差（ADC参考源不准）问题分析报告"}]

    store = Store()

    def search(self, text="", *, business_case_id=None, interface=None, signal=None, device=None, limit=100):
        row = {"knowledge_id": "KO-A0207", "business_case_id": "A0207",
               "title": "模拟量偏差（ADC参考源不准）问题分析报告",
               "design_constraint": "参考源电路满足精度要求", "evidence_refs": ["EV-1"]}
        if business_case_id and business_case_id != "A0207":
            return {"results": []}
        if interface or signal or device:
            return {"results": []}
        if text and not any(piece in row["title"] for piece in text.split()):
            return {"results": []}
        return {"results": [dict(row, match_score=10, match_reasons=[])]}


class FakeAgent:
    def understand(self, query):
        return {"intent": "DESIGN_REUSE", "queries": ["模拟量"],
                "trace": {"task_id": "TEST_ONLY", "run_id": "TEST_ONLY",
                          "provider_calls": 1}}


def setup(monkeypatch, *, token="internal-only"):
    monkeypatch.setenv("HARDWARE_QUERY_AGENT_ENABLED", "1")
    monkeypatch.setenv("HARDWARE_R2_DEPLOYMENT_MODE", "NON_PROD")
    monkeypatch.setenv("HARDWARE_QUERY_AGENT_NONPROD", "1")
    called = []
    def factory():
        called.append(True)
        return FakeAgent()
    form = Formal()
    svc = HardwareAssistedSearch(
        HardwareCaseAIRetrievalService(Cases(), consumption_service=form), form)
    app = FastAPI()
    app.include_router(create_hardware_assisted_query_router(
        svc, trusted_agent_token=token, trusted_agent_factory=factory))
    return TestClient(app), called


def test_public_routes_never_create_provider_with_all_flags(monkeypatch):
    client, called = setup(monkeypatch)
    question = "设计模拟量电路时，有什么经验可以借鉴？"
    for path, params in [
        ("/api/hardware-query/v1/search", {"text": question}),
        ("/api/hardware-query/v1/search", {"text": "A0207"}),
    ]:
        response = client.get(path, params=params)
        assert response.status_code == 200
        assert [x["business_case_id"] for x in response.json()["results"]] == ["A0207"]
    assert not called
    response = client.get("/api/hardware-query/v1/search", params={"text": question})
    assert response.json()["retrieval"]["query_agent"]["status"] == "BLOCKED"
    assert response.json()["retrieval"]["query_agent"]["error_code"] == "QUERY_AGENT_TRUSTED_ROUTE_REQUIRED"


def test_agent_post_requires_all_gates_before_factory(monkeypatch):
    client, called = setup(monkeypatch)
    path = "/api/hardware-query/v1/search-assisted"
    payload = {"text": "设计模拟量电路时，有什么经验可以借鉴？"}
    assert client.post(path, json=payload).status_code == 403
    assert client.post(path, json=payload, headers={"X-Hardware-Query-Token": "incorrect"}).status_code == 403
    assert not called
    monkeypatch.delenv("HARDWARE_QUERY_AGENT_NONPROD")
    response = client.post(path, json=payload, headers={"X-Hardware-Query-Token": "internal-only"})
    assert response.status_code == 503
    assert not called


def test_authorized_agent_post_runs_isolated_agent_and_returns_trace(monkeypatch):
    client, called = setup(monkeypatch)
    r = client.post("/api/hardware-query/v1/search-assisted",
                    json={"text": "设计模拟量电路时，有什么经验可以借鉴？"},
                    headers={"X-Hardware-Query-Token": "internal-only"})
    assert r.status_code == 200, r.text
    assert called == [True]
    data = r.json()
    assert [x["business_case_id"] for x in data["results"]] == ["A0207"]
    assert data["retrieval"]["query_agent"]["status"] == "INVOKED"
    assert data["retrieval"]["query_agent"]["trace"]["run_id"] == "TEST_ONLY"
