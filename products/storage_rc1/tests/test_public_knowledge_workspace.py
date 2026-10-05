from fastapi.testclient import TestClient

from storage_life import public_knowledge
from storage_life.app import app


client = TestClient(app)


def test_fixture_replay_supports_sources_search_detail_and_citation():
    assert client.get("/api/public-knowledge/status").json()["mode"] == "FIXTURE_REPLAY"
    sources = client.get("/api/public-knowledge/sources").json()["sources"]
    assert sources and all(x["classification"] == "PUBLIC" for x in sources)
    hit = client.post("/api/public-knowledge/search", json={"query": "GD25Q64E page size"}).json()["hits"][0]
    assert hit["source_id"] == "fixture-gd25q64e"
    assert client.get("/api/public-knowledge/sources/fixture-gd25q64e").status_code == 200
    citation = client.get("/api/public-knowledge/citations/fixture-citation-page1").json()
    assert citation["text"] and citation["locator"]["page"] == 1


def test_fixture_qa_is_explicitly_synthetic_and_cited():
    result = client.post("/api/public-knowledge/ask", json={"question": "GD25Q64E page size?"}).json()
    assert result["answer_scope"] == "SYNTHETIC_DEMO_ONLY"
    assert result["citations"][0]["citation_id"] == "fixture-citation-page1"


def test_fixture_health_does_not_call_model_provider():
    result = client.get("/api/public-knowledge/provider-health").json()
    assert result["status"] == "not_called"


def test_live_status_exposes_only_model_identity_and_safe_status(monkeypatch):
    def request(mode, path, payload=None, base_url=None):
        if path == "/health":
            return {"status": "ok", "service": "public-knowledge", "version": "0.1"}
        if path == "/config":
            return {"config": {
                "ollama_model": "qwen-text:latest",
                "credential_status": {"secret": "must not escape"},
                "parser_status": "ready",
                "api_key": "secret-value",
            }, "config_hash": "safe-hash"}
        raise AssertionError(path)

    monkeypatch.setattr(public_knowledge, "_request", request)
    result = client.get("/api/public-knowledge/status?mode=LIVE").json()
    assert result["model_name"] == "qwen-text:latest"
    assert result["credential_status"] == "not_reported"
    assert result["parser_status"] == "ready"
    assert "secret" not in str(result)
    assert "api_key" not in result


def test_import_rejects_non_public_before_forwarding(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("non-public content must not reach the service")

    monkeypatch.setattr(public_knowledge, "_request", forbidden)
    response = client.post("/api/public-knowledge/sources/import?mode=LIVE", json={
        "title": "private", "content": "secret", "classification": "INTERNAL"
    })
    assert response.status_code == 422


def test_live_search_does_not_require_qa_provider(monkeypatch):
    monkeypatch.setattr(public_knowledge, "_request", lambda mode, path, payload=None, base_url=None: {
        "hits": [{"hit_id": "h1", "source_id": "s1", "source_revision": "r1", "locator": "page 1", "text": "match", "score": 1.0}],
        "retrieval_snapshot": {"adapter": "sqlite-lexical-reference"},
    })
    response = client.post("/api/public-knowledge/search?mode=LIVE", json={"query": "PRE_EOL_INFO"})
    assert response.status_code == 200
    assert response.json()["hits"][0]["text"] == "match"


def test_live_qa_failure_is_passed_as_fail_closed(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(public_knowledge, "_request", lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(503, "model unavailable")))
    response = client.post("/api/public-knowledge/ask?mode=LIVE", json={"question": "question"})
    assert response.status_code == 503
    assert "model unavailable" in response.text
