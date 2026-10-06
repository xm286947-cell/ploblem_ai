from __future__ import annotations

import json
import stat

import pytest
from fastapi.testclient import TestClient

from public_knowledge_rag import app as service
from public_knowledge_rag.admin_config import ConfigValidationError
from public_knowledge_rag.config import Settings
from public_knowledge_rag.contracts import SearchHit
from public_knowledge_rag.providers import OpenAICompatibleProvider, ProviderUnavailable


KEY = "test-secret-never-return-this"


def settings(protocol="chat_completions"):
    base = Settings.load_base()
    return Settings.with_overrides(base, {
        "provider_type": "openai_compatible",
        "openai_base_url": "https://provider.example/v1",
        "openai_protocol": protocol,
        "openai_model": "provider-model",
        "request_timeout_seconds": 12.0,
        "max_generate_tokens": 200,
        "temperature": 0.2,
    })


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


@pytest.mark.parametrize("protocol,path,response", [
    ("chat_completions", "/chat/completions", {"model": "provider-model", "choices": [{"message": {"content": "Supported by [1]."}}], "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}}),
    ("responses", "/responses", {"model": "provider-model", "output_text": "Supported by [1].", "usage": {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13}}),
])
def test_protocol_posts_to_explicit_endpoint_and_parses_generation(monkeypatch, protocol, path, response):
    seen = {}

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, method=request.method, headers=dict(request.header_items()), payload=json.loads(request.data), timeout=timeout)
        return Response(response)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    hit = SearchHit("hit-1", "source-1", "rev-1", "p. 3", "public excerpt", 1.0)
    provider = OpenAICompatibleProvider(settings(protocol), KEY)
    answer, snapshot = provider.generate("Question", [hit])
    assert answer == "Supported by [1]."
    assert seen["url"] == "https://provider.example/v1" + path
    assert seen["method"] == "POST"
    assert seen["headers"]["Authorization"] == "Bearer " + KEY
    assert seen["payload"]["model"] == "provider-model"
    assert snapshot["protocol"] == protocol
    assert snapshot["input_tokens"] == 9


@pytest.mark.parametrize("protocol,response", [
    ("chat_completions", {"choices": [{"message": {"content": "   "}}]}),
    ("responses", {"output": []}),
])
def test_empty_provider_answer_fails_closed(monkeypatch, protocol, response):
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response(response))
    with pytest.raises(ProviderUnavailable) as exc:
        OpenAICompatibleProvider(settings(protocol), KEY).test_connection()
    assert exc.value.code == "EMPTY_RESPONSE"


def test_test_connection_is_real_small_generation_request(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen.update(payload=json.loads(request.data), url=request.full_url)
        return Response({"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = OpenAICompatibleProvider(settings(), KEY).test_connection()
    assert result["test_response_received"] is True
    assert seen["url"].endswith("/chat/completions")
    assert seen["payload"]["model"] == "provider-model"
    assert seen["payload"]["max_completion_tokens"] == 32


def test_openai_settings_key_is_write_only_local_mode_0600(monkeypatch, tmp_path):
    manager = service.ConfigurationAdmin(base=Settings.load_base(), config_path=tmp_path / "config.local.json", secret_path=tmp_path / "secrets.local.json")
    monkeypatch.setattr(service, "admin_config", manager)
    monkeypatch.setattr(service, "openai_compatible", OpenAICompatibleProvider(settings(), KEY))
    monkeypatch.setattr(service, "settings", settings())

    def generated_test(self):
        return {"test_response_received": True, "protocol": self.settings.openai_protocol, "model": self.settings.openai_model}

    monkeypatch.setattr(OpenAICompatibleProvider, "test_connection", generated_test)
    client = TestClient(service.app)
    candidate = {
        "provider_type": "openai_compatible", "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "ollama-unused", "ollama_model_digest": "a" * 64,
        "openai_base_url": "https://provider.example/v1", "openai_protocol": "responses", "openai_model": "provider-model",
        "request_timeout_seconds": 12, "max_generate_tokens": 200, "temperature": 0.2, "thinking_mode": "disabled",
    }
    response = client.post("/admin/provider/test", json={"config": candidate, "api_key": KEY})
    assert response.status_code == 200, response.text
    assert KEY not in response.text
    test_id = response.json()["test_id"]
    save = client.put("/admin/config", json={"config": candidate, "test_id": test_id, "api_key": KEY})
    assert save.status_code == 200, save.text
    assert KEY not in save.text
    assert KEY not in manager.config_path.read_text()
    assert KEY not in client.get("/admin/config").text
    assert manager.secret_store.path.read_text().find(KEY) >= 0
    assert stat.S_IMODE(manager.secret_store.path.stat().st_mode) == 0o600
    assert KEY not in settings().config_hash()


def test_missing_key_and_changed_key_cannot_save(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    manager = service.ConfigurationAdmin(base=Settings.load_base(), config_path=tmp_path / "config.local.json", secret_path=tmp_path / "secrets.local.json")
    candidate = {"provider_type": "openai_compatible", "ollama_url": "http://localhost:11434", "ollama_model": "unused", "ollama_model_digest": "a" * 64,
                 "openai_base_url": "https://api.openai.com/v1", "openai_protocol": "chat_completions", "openai_model": "gpt-test",
                 "request_timeout_seconds": 10, "max_generate_tokens": 32, "temperature": 0, "thinking_mode": "disabled"}
    with pytest.raises(ConfigValidationError) as missing:
        manager.save(candidate, "no-test")
    assert "CREDENTIAL_MISSING" in str(missing.value.errors)
    first = manager.record_provider_test(candidate, "key-one")
    with pytest.raises(ConfigValidationError) as changed:
        manager.save(candidate, first, candidate_key="key-two")
    assert "Run Test Connection again" in str(changed.value.errors)


def test_http_error_codes_are_classified_without_returning_body(monkeypatch):
    import urllib.error

    def unauthorized(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", unauthorized)
    with pytest.raises(ProviderUnavailable) as exc:
        OpenAICompatibleProvider(settings(), KEY).test_connection()
    assert exc.value.code == "AUTH_FAILED"
    assert KEY not in str(exc.value)


def test_ask_uses_selected_openai_provider_without_ollama_fallback(monkeypatch, tmp_path):
    from public_knowledge_rag.store import Store

    store = Store(tmp_path)
    monkeypatch.setattr(service, "store", store)
    monkeypatch.setattr(service, "retriever", service.SQLiteLexicalRetriever(store))
    selected = settings()
    monkeypatch.setattr(service, "settings", selected)
    class Selected:
        provider_id = "openai_compatible"
        def generate(self, question, contexts):
            return "Answer from selected provider. [1]", {"provider": self.provider_id, "protocol": "chat_completions"}
    class ForbiddenOllama:
        def generate(self, *args):
            raise AssertionError("Ollama fallback was called")
    monkeypatch.setattr(service, "openai_compatible", Selected())
    monkeypatch.setattr(service, "ollama", ForbiddenOllama())
    client = TestClient(service.app)
    assert client.post("/sources/import", json={"title":"Public", "content":"Public excerpt supports the answer.", "classification":"PUBLIC", "source_uri":"https://example.com/source"}).status_code == 200
    result = client.post("/ask", json={"question":"What supports this?"})
    assert result.status_code == 200, result.text
    assert result.json()["model_snapshot"]["provider"] == "openai_compatible"


def test_env_key_is_fallback_and_status_does_not_return_it(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", KEY)
    manager = service.ConfigurationAdmin(base=Settings.load_base(), config_path=tmp_path / "config.local.json", secret_path=tmp_path / "secrets.local.json")
    assert manager.effective_credential() == KEY
    assert manager.secret_store.source() == "ENV"
    state = manager.state(service_version="test", embedding_status="ok", retrieval_adapter="test")
    assert state["current_effective_config"]["sources"]["credential"] == "ENV"
    assert KEY not in json.dumps(state)


def test_clear_credential_requires_explicit_confirmation(monkeypatch, tmp_path):
    manager = service.ConfigurationAdmin(base=Settings.load_base(), config_path=tmp_path / "config.local.json", secret_path=tmp_path / "secrets.local.json")
    monkeypatch.setattr(service, "admin_config", manager)
    manager.secret_store.write(KEY)
    client = TestClient(service.app)
    assert client.post("/admin/provider/credential/clear", json={}).status_code == 422
    result = client.post("/admin/provider/credential/clear", json={"confirm": True})
    assert result.status_code == 200
    assert result.json()["credential_status"] == "MISSING"
    assert not manager.secret_store.path.exists()
