from fastapi.testclient import TestClient

from knowledge_consumer_gateway import app as gateway_module


client = TestClient(gateway_module.app)


def test_gateway_capabilities_are_consumer_only(monkeypatch):
    monkeypatch.setattr(gateway_module, "_upstream", lambda path, **kw: {"status": "ok", "service": "public-knowledge", "version": "x"})
    result = client.get("/capabilities")
    assert result.status_code == 200
    body = result.json()
    assert body["contract"] == "knowledge-consumer/v1"
    assert body["read_only_consumer_api"] is True
    assert body["endpoints"]["search"] == "/search"


def test_gateway_proxies_health_sources_search_and_ask(monkeypatch):
    calls = []
    def fake(path, payload=None, **kw):
        calls.append((path, payload, kw))
        if path == "/health": return {"status": "ok", "service": "public-knowledge", "version": "1"}
        if path == "/sources": return {"sources": []}
        if path == "/search": return {"hits": []}
        if path == "/ask": return {"answer": "mock", "citations": []}
        return {}
    monkeypatch.setattr(gateway_module, "_upstream", fake)
    assert client.get("/health").status_code == 200
    assert client.get("/sources").json() == {"sources": []}
    assert client.post("/search", json={"query": "WAF"}).status_code == 200
    assert client.post("/ask", json={"question": "WAF"}).json()["answer"] == "mock"
    assert [c[0] for c in calls].count("/ask") == 1


def test_t08_gateway_denies_admin_routes(monkeypatch):
    monkeypatch.setattr(gateway_module, "_upstream", lambda *a, **k: (_ for _ in ()).throw(AssertionError("admin forwarded")))
    assert client.get("/admin/config").status_code == 404
    assert client.get("/providers/active/health").status_code == 404


def test_t09_gateway_denies_delete_source(monkeypatch):
    monkeypatch.setattr(gateway_module, "_upstream", lambda *a, **k: (_ for _ in ()).throw(AssertionError("delete forwarded")))
    assert client.delete("/sources/source-1").status_code in {404, 405}


def test_gateway_rejects_path_injection_and_invalid_contract(monkeypatch):
    monkeypatch.setattr(gateway_module, "_upstream", lambda *a, **k: {})
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException):
        gateway_module._valid_id("../admin")
    assert client.post("/search", json={"query": "x", "top_k": 999}).status_code == 422
