from __future__ import annotations

import signal

from fastapi.testclient import TestClient

from public_knowledge_rag import app as service
from public_knowledge_rag.admin_config import ConfigurationAdmin
from public_knowledge_rag.config import Settings


def restart_client(monkeypatch, tmp_path, *, host="http://127.0.0.1"):
    manager = ConfigurationAdmin(
        base=Settings.load_base(),
        config_path=tmp_path / "config.local.json",
        secret_path=tmp_path / "secrets.local.json",
    )
    manager.apply_state = lambda: "RESTART_REQUIRED"
    monkeypatch.setattr(service, "admin_config", manager)
    return TestClient(service.app, base_url=host), manager


def test_restart_is_unavailable_for_direct_unsupervised_start(monkeypatch, tmp_path):
    monkeypatch.delenv("PKR_RESTART_STRATEGY", raising=False)
    client, _ = restart_client(monkeypatch, tmp_path)
    state = client.get("/admin/config").json()
    assert state["apply_state"] == "RESTART_REQUIRED"
    assert state["restart_capability"] == {"strategy": "UNAVAILABLE", "available": False}
    result = client.post("/admin/restart", json={"command": "anything"})
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "RESTART_UNAVAILABLE"


def test_supervised_restart_only_sends_sigterm_to_current_process_after_response(monkeypatch, tmp_path):
    monkeypatch.setenv("PKR_RESTART_STRATEGY", "supervised_process_exit")
    client, manager = restart_client(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(service.os, "kill", lambda pid, signum: seen.append((pid, signum)))

    state = client.get("/admin/config").json()
    assert state["restart_capability"] == {"strategy": "SUPERVISED_PROCESS_EXIT", "available": True}
    result = client.post("/admin/restart")
    assert result.status_code == 202
    assert result.json()["state"] == "RESTARTING"
    assert result.json()["strategy"] == "SUPERVISED_PROCESS_EXIT"
    assert seen == [(service.os.getpid(), signal.SIGTERM)]
    assert not manager.config_path.exists()
    assert not manager.secret_store.path.exists()


def test_restart_is_blocked_for_non_loopback_host(monkeypatch, tmp_path):
    monkeypatch.setenv("PKR_RESTART_STRATEGY", "supervised_process_exit")
    client, _ = restart_client(monkeypatch, tmp_path, host="http://public.example")
    result = client.post("/admin/restart")
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "RESTART_UNAVAILABLE"


def test_restart_returns_no_restart_required_when_config_is_applied(monkeypatch, tmp_path):
    monkeypatch.setenv("PKR_RESTART_STRATEGY", "supervised_process_exit")
    client, manager = restart_client(monkeypatch, tmp_path)
    manager.apply_state = lambda: "APPLIED"
    result = client.post("/admin/restart")
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "NO_RESTART_REQUIRED"


def test_restart_request_cannot_change_config(monkeypatch, tmp_path):
    monkeypatch.delenv("PKR_RESTART_STRATEGY", raising=False)
    client, manager = restart_client(monkeypatch, tmp_path)
    before = {"saved": manager.saved, "secret_exists": manager.secret_store.path.exists()}
    result = client.post("/admin/restart", json={"provider_type": "openai_compatible", "api_key": "not-a-secret"})
    assert result.status_code == 503
    assert manager.saved == before["saved"]
    assert manager.secret_store.path.exists() == before["secret_exists"]
    if manager.config_path.exists():
        assert "not-a-secret" not in manager.config_path.read_text(encoding="utf-8")
