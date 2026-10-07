from __future__ import annotations

import pytest
from fastapi import HTTPException

from storage_life import knowledge_service_client as client_module
from storage_life.knowledge_service_client import (
    KnowledgeServiceClient,
    KnowledgeServiceError,
    canonicalize_service_url,
    resolve_service_url,
)


def test_t01_single_url_has_highest_environment_precedence(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_SERVICE_URL", "HTTPS://Knowledge.Example:443/")
    monkeypatch.setenv("PUBLIC_KNOWLEDGE_SERVICE_URL", "http://legacy:8080")
    monkeypatch.setenv("PUBLIC_KNOWLEDGE_API_URL", "http://old:7000")
    monkeypatch.setattr(client_module, "_config_file_value", lambda: "http://saved:9001")
    assert resolve_service_url() == "https://knowledge.example"


def test_t02_legacy_service_url_is_compatible(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_SERVICE_URL", raising=False)
    monkeypatch.setenv("PUBLIC_KNOWLEDGE_SERVICE_URL", "http://legacy:9000/")
    monkeypatch.setenv("PUBLIC_KNOWLEDGE_API_URL", "http://old:9000")
    monkeypatch.setattr(client_module, "_config_file_value", lambda: None)
    assert resolve_service_url() == "http://legacy:9000"


def test_legacy_api_url_and_default_are_compatible(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_SERVICE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_KNOWLEDGE_SERVICE_URL", raising=False)
    monkeypatch.setenv("PUBLIC_KNOWLEDGE_API_URL", "http://old:9000")
    monkeypatch.setattr(client_module, "_config_file_value", lambda: None)
    assert resolve_service_url() == "http://old:9000"
    monkeypatch.delenv("PUBLIC_KNOWLEDGE_API_URL")
    assert resolve_service_url() == "http://127.0.0.1:9000"


def test_url_is_canonical_and_rejects_credentials_paths_and_queries():
    assert canonicalize_service_url("HTTP://Mac.Example:80/") == "http://mac.example"
    for value in (
        "ftp://mac.example", "http://user:pass@mac.example", "http://mac.example/path",
        "http://mac.example/?token=x", "http://mac.example/#frag", "http://mac.example:bad",
    ):
        with pytest.raises(KnowledgeServiceError) as exc:
            canonicalize_service_url(value)
        assert exc.value.code == "KNOWLEDGE_SERVICE_URL_INVALID"


def test_t03_health_uses_single_service_origin(monkeypatch):
    client = KnowledgeServiceClient("http://mac.example:9001")
    seen = []
    monkeypatch.setattr(client, "_raw", lambda path, **kw: seen.append((path, kw)) or {"status": "ok", "service": "public-knowledge"})
    assert client.health()["status"] == "ok"
    assert seen[0][0] == "/health"


def test_t04_capabilities_discovery_validates_contract(monkeypatch):
    client = KnowledgeServiceClient("http://mac.example:9001")
    payload = {
        "contract": "knowledge-consumer/v1",
        "endpoints": {
            "health": "/health", "search": "/search", "ask": "/ask",
            "sources": "/sources", "citation": "/citations/{citation_id}",
        },
    }
    monkeypatch.setattr(client, "_raw", lambda path, **kw: payload)
    client_module.reset_capabilities_cache()
    caps = client.capabilities(refresh=True)
    assert caps.mode == "DISCOVERED"
    assert caps.payload["contract"] == "knowledge-consumer/v1"


def test_t05_search_uses_discovered_endpoint(monkeypatch):
    client = KnowledgeServiceClient("http://mac.example:9001")
    monkeypatch.setattr(client, "capabilities", lambda **kw: client_module.ServiceCapabilities({"endpoints": {"search": "/v2/find"}}, "DISCOVERED"))
    seen = {}
    monkeypatch.setattr(client, "endpoint", lambda name, **ids: "/v2/find")
    monkeypatch.setattr(client, "_raw", lambda path, payload=None, **kw: seen.update(path=path, payload=payload) or {"hits": []})
    assert client.request("/search", {"query": "WAF", "top_k": 1}) == {"hits": []}
    assert seen["path"] == "/v2/find"


def test_t10_retrieval_timeout_and_retry_are_bounded(monkeypatch):
    client = KnowledgeServiceClient("http://mac.example:9001")
    monkeypatch.setattr(client, "endpoint", lambda name, **ids: "/search")
    seen = {}
    monkeypatch.setattr(client, "_raw", lambda path, payload=None, **kw: seen.update(path=path, **kw) or {"hits": []})
    assert client.search("P/E cycles", top_k=1)["hits"] == []
    assert seen["timeout"] == 8
    assert seen["attempts"] == 2


def test_t06_ask_contract_uses_long_bounded_timeout_without_retry(monkeypatch):
    client = KnowledgeServiceClient("http://mac.example:9001")
    monkeypatch.setattr(client, "endpoint", lambda name, **ids: "/ask")
    seen = {}
    monkeypatch.setattr(client, "_raw", lambda path, payload=None, **kw: seen.update(path=path, payload=payload, **kw) or {"answer": "ok", "citations": []})
    assert client.request("/ask", {"question": "WAF"})["answer"] == "ok"
    assert seen["timeout"] == 120
    assert seen["attempts"] == 1


def test_capability_fallback_only_on_not_found(monkeypatch):
    client = KnowledgeServiceClient("http://legacy.example:9000")
    monkeypatch.setattr(client, "_raw", lambda *a, **k: (_ for _ in ()).throw(KnowledgeServiceError("KNOWLEDGE_SERVICE_UNREACHABLE", "HTTP 404: missing")))
    client_module.reset_capabilities_cache()
    assert client.capabilities(refresh=True).mode == "LEGACY_CONTRACT"
    monkeypatch.setattr(client, "_raw", lambda *a, **k: (_ for _ in ()).throw(KnowledgeServiceError("KNOWLEDGE_SERVICE_UNREACHABLE", "connection refused")))
    with pytest.raises(KnowledgeServiceError):
        client.capabilities(refresh=True)


def test_invalid_capabilities_fail_closed(monkeypatch):
    client = KnowledgeServiceClient("http://mac.example:9001")
    monkeypatch.setattr(client, "_raw", lambda *a, **k: {"contract": "unknown", "endpoints": {}})
    client_module.reset_capabilities_cache()
    with pytest.raises(KnowledgeServiceError) as exc:
        client.capabilities(refresh=True)
    assert exc.value.code == "KNOWLEDGE_SERVICE_CONTRACT_INVALID"


def test_capabilities_cannot_redirect_consumer_operation_into_admin_path(monkeypatch):
    client = KnowledgeServiceClient("http://mac.example:9001")
    payload = {
        "contract": "knowledge-consumer/v1",
        "endpoints": {"health": "/health", "search": "/admin/config", "ask": "/ask", "sources": "/sources"},
    }
    monkeypatch.setattr(client, "_raw", lambda *a, **k: payload)
    client_module.reset_capabilities_cache()
    with pytest.raises(KnowledgeServiceError):
        client.capabilities(refresh=True)


def test_client_never_follows_redirect(monkeypatch):
    client = KnowledgeServiceClient("http://mac.example:9001")

    class RedirectingOpener:
        def open(self, request, timeout):
            from urllib.error import HTTPError
            raise HTTPError(request.full_url, 302, "Found", {"Location": "http://other.example/"}, None)

    monkeypatch.setattr(client_module, "_OPENER", RedirectingOpener())
    with pytest.raises(KnowledgeServiceError) as exc:
        client._raw("/health")
    assert "重定向" in exc.value.detail


def test_untrusted_browser_service_override_is_rejected(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_SERVICE_URL", "http://trusted.example:9001")
    monkeypatch.setenv("PUBLIC_KNOWLEDGE_ALLOWED_URLS", "http://attacker.example")
    from storage_life.public_knowledge import _url
    with pytest.raises(HTTPException):
        _url("http://attacker.example")
