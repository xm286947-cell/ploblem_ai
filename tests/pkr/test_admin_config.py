from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from public_knowledge_rag import app as service
from public_knowledge_rag.admin_config import ConfigurationAdmin
from public_knowledge_rag.config import Settings, UI_CONFIG_FIELDS


def setup_admin_client(monkeypatch, tmp_path):
    manager = ConfigurationAdmin(base=Settings.load_base(), config_path=tmp_path / "config.local.json", secret_path=tmp_path / "secrets.local.json")
    monkeypatch.setattr(service, "admin_config", manager)
    return TestClient(service.app), manager


def draft_config():
    return {
        "provider_type": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen-test:latest",
        "ollama_model_digest": "a" * 64,
        "openai_base_url": "https://api.openai.com/v1",
        "openai_protocol": "chat_completions",
        "openai_model": "gpt-test",
        "request_timeout_seconds": 20,
        "max_generate_tokens": 700,
        "temperature": 0,
        "thinking_mode": "disabled",
    }


def test_settings_page_and_admin_read_are_secret_free(monkeypatch, tmp_path):
    client, _ = setup_admin_client(monkeypatch, tmp_path)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Generation" in page.text
    assert "PUBLIC_ONLY" in page.text
    assert "Test Connection" in page.text

    response = client.get("/admin/config")
    assert response.status_code == 200
    body = response.json()
    assert body["current_effective_config"]["service"]["source_class_gate"] == "PUBLIC_ONLY"
    assert body["current_effective_config"]["generation"]["credential_status"] == "NOT_REQUIRED"
    assert body["current_effective_config"]["generation"]["provider_type"] == "ollama"
    assert "LOCAL_UI_OVERRIDE" in body["current_effective_config"]["sources"].values() or body["apply_state"] == "APPLIED"
    assert "api_key" not in response.text.lower()
    assert "secret" not in response.text.lower()


def test_invalid_config_does_not_replace_last_known_good(monkeypatch, tmp_path):
    config_path = tmp_path / "config.local.json"
    previous = {"ollama_model": "known-good:tag", "ollama_url": "http://192.168.1.100:11434"}
    config_path.write_text(json.dumps(previous), encoding="utf-8")
    client, manager = setup_admin_client(monkeypatch, tmp_path)
    assert manager.saved["ollama_model"] == previous["ollama_model"]
    assert manager.saved["ollama_url"] == previous["ollama_url"]

    invalid = {**draft_config(), "ollama_url": "http://user:password@example.com/?token=unsafe"}
    response = client.post("/admin/config/validate", json=invalid)
    assert response.status_code == 422
    assert config_path.read_text(encoding="utf-8") == json.dumps(previous)
    assert "password" not in response.text
    assert "token=unsafe" not in response.text


def test_local_override_precedes_environment_and_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_URL", "http://env-provider.example:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "env-model:tag")
    config_path = tmp_path / "config.local.json"
    config_path.write_text(json.dumps({"ollama_model": "local-model:tag"}), encoding="utf-8")
    manager = ConfigurationAdmin(base=Settings.load_base(), config_path=config_path)
    assert manager.effective.ollama_url == "http://env-provider.example:11434"
    assert manager.effective.ollama_model == "local-model:tag"
    state = manager.state(service_version="test", embedding_status="unconfigured", retrieval_adapter="test")
    assert state["current_effective_config"]["sources"]["ollama_model"] == "LOCAL_UI_OVERRIDE"
    assert state["current_effective_config"]["sources"]["ollama_url"] == "ENV"


def test_config_save_requires_successful_test_and_reports_restart(monkeypatch, tmp_path):
    client, manager = setup_admin_client(monkeypatch, tmp_path)
    candidate = draft_config()
    monkeypatch.setattr(service.OllamaProvider, "version", lambda self: {"version": "0.9.0"})
    monkeypatch.setattr(
        service.OllamaProvider,
        "models",
        lambda self: [{"name": self.settings.ollama_model, "digest": self.settings.ollama_model_digest}],
    )

    validated = client.post("/admin/config/validate", json=candidate)
    assert validated.status_code == 200 and validated.json()["valid"] is True
    tested = client.post("/admin/provider/test", json=candidate)
    assert tested.status_code == 200
    test_result = tested.json()
    assert test_result["ok"] is True
    assert test_result["model_name"] == candidate["ollama_model"]
    assert test_result["model_digest"] == candidate["ollama_model_digest"]

    save = client.put("/admin/config", json={"config": candidate, "test_id": test_result["test_id"]})
    assert save.status_code == 200
    assert save.json()["apply_state"] == "RESTART_REQUIRED"
    assert manager.effective.ollama_model != candidate["ollama_model"]
    assert save.json()["saved_config"]["ollama_model"] == candidate["ollama_model"]
    persisted = json.loads(manager.config_path.read_text(encoding="utf-8"))
    assert persisted["ollama_model"] == candidate["ollama_model"]
    assert not (set(persisted) - set(UI_CONFIG_FIELDS))
    assert "secret" not in json.dumps(persisted).lower()
    assert "api_key" not in json.dumps(persisted).lower()

    # A fresh process consumes the local override as the effective config.
    restarted = ConfigurationAdmin(base=manager.base, config_path=manager.config_path)
    assert restarted.effective.ollama_model == candidate["ollama_model"]
    assert restarted.apply_state() == "APPLIED"


def test_switching_to_ollama_does_not_require_or_clear_saved_openai_key(tmp_path):
    manager = ConfigurationAdmin(
        base=Settings.load_base(),
        config_path=tmp_path / "config.local.json",
        secret_path=tmp_path / "secrets.local.json",
    )
    saved_key = "write-only-openai-key"
    manager.secret_store.write(saved_key)
    candidate = manager.validate(draft_config())

    test_id = manager.record_provider_test(candidate, None)
    assert manager.save(candidate, test_id) == "RESTART_REQUIRED"

    assert manager.secret_store.local_key() == saved_key
    assert "api_key" not in manager.config_path.read_text(encoding="utf-8").lower()
    assert saved_key not in manager.config_path.read_text(encoding="utf-8")


def test_provider_model_mismatch_cannot_be_saved(monkeypatch, tmp_path):
    client, _ = setup_admin_client(monkeypatch, tmp_path)
    candidate = draft_config()
    monkeypatch.setattr(service.OllamaProvider, "version", lambda self: {"version": "0.9.0"})
    monkeypatch.setattr(service.OllamaProvider, "models", lambda self: [{"name": "other:tag", "digest": "b" * 64}])
    tested = client.post("/admin/provider/test", json=candidate)
    assert tested.status_code == 200
    assert tested.json()["provider_reachable"] is True
    assert tested.json()["model_found"] is False
    assert "test_id" not in tested.json()

    save = client.put("/admin/config", json={"config": candidate, "test_id": "invented"})
    assert save.status_code == 422


def test_provider_failure_is_redacted_and_preserves_saved_config(monkeypatch, tmp_path, caplog):
    config_path = tmp_path / "config.local.json"
    previous = {"ollama_model": "known-good:tag"}
    config_path.write_text(json.dumps(previous), encoding="utf-8")
    client, manager = setup_admin_client(monkeypatch, tmp_path)
    secret_marker = "private-test-marker"

    def fail_connection(self):
        raise service.ProviderUnavailable("connection error containing " + secret_marker)

    monkeypatch.setattr(service.OllamaProvider, "version", fail_connection)
    with caplog.at_level(logging.WARNING):
        response = client.post("/admin/provider/test", json=draft_config())

    assert response.status_code == 503
    assert secret_marker not in response.text
    assert secret_marker not in caplog.text
    assert config_path.read_text(encoding="utf-8") == json.dumps(previous)
    assert manager.saved["ollama_model"] == previous["ollama_model"]


def test_admin_config_does_not_accept_disabling_public_only(monkeypatch, tmp_path):
    client, _ = setup_admin_client(monkeypatch, tmp_path)
    candidate = {**draft_config(), "source_class_gate": "ALL"}
    response = client.post("/admin/config/validate", json=candidate)
    assert response.status_code == 422
