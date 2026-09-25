from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from storage_life import product_api
from storage_life.app import app
from storage_life.knowledge_release import KnowledgeReleaseConsumer


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "knowledge_release" / "current"


def test_complete_candidate_starts_with_formal_knowledge_ready(monkeypatch):
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(RELEASE))
    status = KnowledgeReleaseConsumer.current().status()
    assert status["available"] is True
    assert status["status"] == "READY"
    assert status["knowledge_release_version"] == "KP-STORAGE-RC1-VALIDATION-001"
    assert status["snapshot_hash"] == "938efdaf21e7a50d694872a115125bf81f3ae27b7b754070226f24b4edb08fc8"


def test_storage_exposes_unified_knowledge_processing_on_same_port():
    client = TestClient(app)
    response = client.get("/knowledge-production/sources")
    assert response.status_code == 200
    assert "Knowledge Production" in response.text
    assert "/knowledge-production/candidates" in response.text


def test_formal_knowledge_is_consumed_by_diagnostics(monkeypatch):
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(RELEASE))
    monkeypatch.setattr(
        product_api.ai,
        "expected_fields",
        lambda _dtype: [
            {
                "canonical_name": "percentage_used",
                "parameter_name": "Percentage Used",
                "role": "diagnostic",
                "note": "NVMe lifetime consumption indicator",
            }
        ],
    )
    result = product_api.diagnostics(device_type="SSD")
    assert result["items"]
    row = result["items"][0]
    assert row["formal_knowledge"]["status"] == "MATCHED"
    assert row["formal_knowledge"]["knowledge_release_version"] == "KP-STORAGE-RC1-VALIDATION-001"
    assert row["formal_knowledge"]["evidence_refs"] == ["EVD-98019c742313d72837696c0b"]
    assert row["runtime_observation"]["status"] == "UNKNOWN"
    assert row["runtime_observation"]["code"] == "RUNTIME_OBSERVATION_UNAVAILABLE"


def test_device_lifecycle_is_derived_from_existing_review_workflow(monkeypatch):
    monkeypatch.setattr(
        product_api.core,
        "specification_workflow_status",
        lambda _device_id: {"formal_ready": False, "status": "pending_confirmation"},
    )
    draft = product_api._device_lifecycle("DRAFT-1")
    assert draft["status"] == "DRAFT"
    assert draft["reason"] == "REVIEW_REQUIRED"

    monkeypatch.setattr(
        product_api.core,
        "specification_workflow_status",
        lambda _device_id: {"formal_ready": True, "status": "confirmed"},
    )
    formal = product_api._device_lifecycle("FORMAL-1")
    assert formal["status"] == "FORMAL_READY"
    assert formal["reason"] == "HUMAN_CONFIRMED_DEVICE_FACT_READY"


def test_product_ui_contains_complete_knowledge_and_lifetime_assembly():
    html = (ROOT / "storage_life" / "index.html").read_text(encoding="utf-8")
    assert "正式 Knowledge Production" in html
    assert "SourceDocument" in html
    assert "AI Extraction → Candidate" in html
    assert "/knowledge-production/candidates" in html
    assert "生成并激活 Formal Release" in html
    assert "Runtime Observation" in html
    assert "寿命 / 风险结论" in html
    assert "Device Lifecycle" in html
