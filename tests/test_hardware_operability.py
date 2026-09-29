from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import services.hardware_operability as operability
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


class _HealthyResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _app(tmp_path):
    return create_p0_app(
        tmp_path / "quality.db",
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_tree_upload_dir=tmp_path / "tree",
        hardware_case_source_root=tmp_path / "sources",
        enabled_domains={"HARDWARE_CASE"},
    )


def _runtime_config(tmp_path: Path) -> Path:
    path = tmp_path / "model.local.yaml"
    path.write_text(
        """active_model: hardware_ci
models:
  hardware_ci:
    provider: openai_compatible
    base_url: http://127.0.0.1:9/v1
    api_key_env: HARDWARE_CASE_API_KEY
    model: mock-model
    temperature: 0
    max_tokens: 8192
""",
        encoding="utf-8",
    )
    return path


def test_health_is_liveness_only_and_ready_fails_closed_without_config(tmp_path, monkeypatch):
    client = TestClient(_app(tmp_path))

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "HEALTHY", "service": "HARDWARE_CASE"}

    monkeypatch.delenv("HARDWARE_CASE_MODEL_CONFIG", raising=False)
    monkeypatch.delenv("HARDWARE_KNOWLEDGE_BASE_URL", raising=False)
    monkeypatch.delenv("HARDWARE_KNOWLEDGE_RELEASE_VERSION", raising=False)
    monkeypatch.delenv("HARDWARE_KNOWLEDGE_READINESS_URL", raising=False)

    ready = client.get("/ready")
    assert ready.status_code == 503
    payload = ready.json()
    assert payload["status"] == "UNREADY"
    assert payload["dependencies"]["HARDWARE_DB"]["status"] == "READY"
    assert payload["dependencies"]["PUBLIC_CONTRACT"]["status"] == "READY"
    assert payload["dependencies"]["UNIFIED_RUNTIME_CONFIG"]["status"] == "UNREADY"
    assert payload["dependencies"]["UNIFIED_KNOWLEDGE"]["status"] == "UNREADY"


def test_ready_passes_with_valid_runtime_config_and_knowledge_probe(tmp_path, monkeypatch):
    model_config = _runtime_config(tmp_path)
    monkeypatch.setenv("HARDWARE_CASE_MODEL_CONFIG", str(model_config))
    monkeypatch.setenv("HARDWARE_CASE_API_KEY", "TOP_SECRET_TEST_VALUE")
    monkeypatch.setenv("HARDWARE_KNOWLEDGE_BASE_URL", "http://127.0.0.1:19091")
    monkeypatch.setenv("HARDWARE_KNOWLEDGE_RELEASE_VERSION", "KNOWLEDGE_TEST_R1")
    monkeypatch.setenv("HARDWARE_KNOWLEDGE_READINESS_URL", "http://127.0.0.1:19091/health")
    monkeypatch.setattr(operability, "urlopen", lambda request, timeout=0: _HealthyResponse())

    client = TestClient(_app(tmp_path))
    ready = client.get("/ready")
    assert ready.status_code == 200
    payload = ready.json()
    assert payload["status"] == "READY"
    assert all(item["status"] == "READY" for item in payload["dependencies"].values())

    rendered = ready.text
    assert "TOP_SECRET_TEST_VALUE" not in rendered
    assert "127.0.0.1:19091" not in rendered


def test_knowledge_dependency_unavailable_is_explicit_and_fail_closed(tmp_path, monkeypatch):
    model_config = _runtime_config(tmp_path)
    monkeypatch.setenv("HARDWARE_CASE_MODEL_CONFIG", str(model_config))
    monkeypatch.setenv("HARDWARE_CASE_API_KEY", "test-secret")
    monkeypatch.setenv("HARDWARE_KNOWLEDGE_BASE_URL", "http://127.0.0.1:19091")
    monkeypatch.setenv("HARDWARE_KNOWLEDGE_RELEASE_VERSION", "KNOWLEDGE_TEST_R1")
    monkeypatch.setenv("HARDWARE_KNOWLEDGE_READINESS_URL", "http://127.0.0.1:19091/health")

    def _unavailable(*args, **kwargs):
        raise OSError("dependency down")

    monkeypatch.setattr(operability, "urlopen", _unavailable)

    client = TestClient(_app(tmp_path))
    ready = client.get("/ready")
    assert ready.status_code == 503
    dependency = ready.json()["dependencies"]["UNIFIED_KNOWLEDGE"]
    assert dependency == {"status": "UNREADY", "error_code": "KNOWLEDGE_UNAVAILABLE"}


def test_contract_version_visible_compatible_and_unknown_version_rejected(tmp_path):
    client = TestClient(_app(tmp_path))

    response = client.get("/api/public/hardware/v1/contract")
    assert response.status_code == 200
    payload = response.json()
    assert payload["public_contract_version"] == "hardware-public-consumer/v1"
    assert payload["public_api_version"] == "v1"
    assert payload["product_version"] == "MVP_V0.1"
    assert payload["schema_version"] == "HARDWARE_SCHEMA_V1"
    assert payload["compatibility"]["v1"]["status"] == "ACTIVE"
    assert payload["compatibility"]["v1"]["deprecated"] is False
    assert payload["product_version"] != payload["public_contract_version"]

    assert client.get("/api/public/hardware/v999/contract").status_code == 404
