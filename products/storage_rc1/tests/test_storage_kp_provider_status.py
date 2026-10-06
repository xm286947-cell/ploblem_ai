from __future__ import annotations

from fastapi.testclient import TestClient

from storage_life import app as app_module
from storage_life import runtime_bridge


client = TestClient(app_module.app)


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _runtime_status(*, configured: bool = True) -> dict:
    return {
        "configured": configured,
        "execution_mode": "runtime",
        "execution_mode_source": "environment",
        "provider": "openai_compatible" if configured else None,
        "profile": "qwen_prod" if configured else None,
        "model": "qwen3.8-max" if configured else None,
        "base_url": (
            "http://127.0.0.1:8000/v1" if configured else None
        ),
        "api_key_env": "TEST_PROVIDER_KEY",
        "api_key_present": False,
        "runtime": {
            "model_config": "/private/config/model.local.yaml",
        },
        "knowledge_production": {
            "agent_id": "knowledge.production.extract",
        },
    }


def test_kp_provider_status_reports_safe_reachable_transport(
    monkeypatch,
):
    monkeypatch.setattr(
        runtime_bridge,
        "status",
        lambda: _runtime_status(),
    )
    seen = {}

    def connect(address, timeout):
        seen["address"] = address
        seen["timeout"] = timeout
        return _Connection()

    monkeypatch.setattr(
        app_module.socket,
        "create_connection",
        connect,
    )

    response = client.get(
        "/api/product/knowledge-production/provider-status"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["execution_ready"] is True
    assert payload["agent_id"] == "knowledge.production.extract"
    assert payload["model_ref"] == "qwen_prod"
    assert payload["provider"] == "openai_compatible"
    assert payload["model"] == "qwen3.8-max"
    assert payload["endpoint"] == {
        "scheme": "http",
        "host": "127.0.0.1",
        "port": 8000,
    }
    assert payload["transport_status"] == "REACHABLE"
    assert payload["credential_present"] is False
    assert payload["next_action"] == "READY_TO_EXTRACT"
    assert payload["boundary"]["second_agent_config"] is False
    assert payload["boundary"]["secret_exposed"] is False
    assert seen["address"] == ("127.0.0.1", 8000)
    serialized = str(payload)
    assert "/private/config/model.local.yaml" not in serialized
    assert "TEST_PROVIDER_KEY" in serialized


def test_kp_provider_status_fails_visible_when_transport_unreachable(
    monkeypatch,
):
    monkeypatch.setattr(
        runtime_bridge,
        "status",
        lambda: _runtime_status(),
    )

    def unreachable(*args, **kwargs):
        raise OSError("connection refused: secret detail")

    monkeypatch.setattr(
        app_module.socket,
        "create_connection",
        unreachable,
    )

    payload = client.get(
        "/api/product/knowledge-production/provider-status"
    ).json()

    assert payload["configured"] is True
    assert payload["execution_ready"] is False
    assert payload["transport_status"] == "UNREACHABLE"
    assert payload["next_action"] == "START_OR_FIX_AI_PROVIDER"
    assert "secret detail" not in str(payload)


def test_kp_provider_status_does_not_probe_when_runtime_unconfigured(
    monkeypatch,
):
    monkeypatch.setattr(
        runtime_bridge,
        "status",
        lambda: _runtime_status(configured=False),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("unconfigured Runtime must not probe network")

    monkeypatch.setattr(
        app_module.socket,
        "create_connection",
        forbidden,
    )

    payload = client.get(
        "/api/product/knowledge-production/provider-status"
    ).json()

    assert payload["configured"] is False
    assert payload["execution_ready"] is False
    assert payload["transport_status"] == "NOT_CHECKED"
    assert payload["next_action"] == "FIX_RUNTIME_AGENT_CONFIG"
