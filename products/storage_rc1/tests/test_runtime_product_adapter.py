from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from storage_life import ai, runtime_bridge
from storage_life.app import app
from storage_life.runtime_domain_strategy import EMMC_FIELD_ORDER


def test_runtime_mode_routes_storage_ai_call_through_bridge(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    seen = {}

    def fake_call(instructions, payload, schema):
        seen.update({"instructions": instructions, "payload": payload, "schema": schema})
        return {"ok": True}

    monkeypatch.setattr(runtime_bridge, "call_json", fake_call)
    result = ai._call("instruction", {"x": 1}, {"type": "object"})
    assert result == {"ok": True}
    assert seen["payload"] == {"x": 1}


def test_runtime_mode_forbids_storage_side_provider_client(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    with pytest.raises(ai.AIResponseError, match="不接受 Storage 侧 provider client 注入"):
        ai._call("instruction", {}, {"type": "object"}, client=object())


def test_runtime_bridge_error_maps_to_storage_error_surface(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")

    def fail(*_args, **_kwargs):
        raise runtime_bridge.RuntimeBridgeCallError(
            "bad json", code="PROVIDER_JSON_INVALID", category="VALIDATION", retryable=True
        )

    monkeypatch.setattr(runtime_bridge, "call_json", fail)
    with pytest.raises(ai.AIResponseError, match="VALIDATION/PROVIDER_JSON_INVALID"):
        ai._call("instruction", {}, {"type": "object"})


def test_product_runtime_contract_endpoint_exposes_frozen_37_fields():
    client = TestClient(app)
    response = client.get("/api/v1/runtime/contract/emmc")
    assert response.status_code == 200
    data = response.json()
    assert data["field_count"] == 37
    assert data["fields"] == list(EMMC_FIELD_ORDER)
    assert len(data["atomic_groups"]) == 6
    assert "retry" in data["runtime_boundary"]["runtime_owns"]
    assert "schema" in data["runtime_boundary"]["storage_owns"]


def test_runtime_bridge_accepts_user_owned_model_config(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime_repo"
    fallback = runtime_root / "config" / "runtime" / "model.yaml"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("active_model: qwen_prod\nmodels: {}\n", encoding="utf-8")

    local = tmp_path / "private" / "model.local.yaml"
    local.parent.mkdir(parents=True)
    local.write_text(
        "active_model: qwen_prod\nmodels:\n  qwen_prod:\n    provider: openai_compatible\n    base_url: https://workspace.example/v1\n    api_key: local-secret\n    model: qwen3.8-max\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("STORAGE_MODEL_CONFIG", str(local))
    assert runtime_bridge.model_config_path(runtime_root) == local.resolve()


def test_runtime_bridge_defaults_to_public_runtime_model_config(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime_repo"
    fallback = runtime_root / "config" / "runtime" / "model.yaml"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("active_model: qwen_prod\nmodels: {}\n", encoding="utf-8")

    monkeypatch.delenv("STORAGE_MODEL_CONFIG", raising=False)
    assert runtime_bridge.model_config_path(runtime_root) == fallback.resolve()
